import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import export, service
from ..auth import current_teacher
from ..config import Settings, get_settings
from ..db import get_db
from ..models import Choice, Question, Quiz, User
from ..public_base import public_base_url
from ..schemas import (
    QuestionEdit,
    QuestionIn,
    QuestionOrder,
    QuestionTeacherOut,
    QuizIn,
    QuizOut,
)

router = APIRouter(prefix="/api/quizzes", tags=["quizzes"])


def _own_quiz(db: Session, quiz_id: int, teacher: User) -> Quiz:
    quiz = db.get(Quiz, quiz_id)
    if quiz is None or quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quiz not found")
    return quiz


@router.get("", response_model=list[QuizOut])
def list_quizzes(
    db: Session = Depends(get_db), teacher: User = Depends(current_teacher)
) -> list[Quiz]:
    return list(db.scalars(select(Quiz).where(Quiz.owner_id == teacher.id)))


@router.post("", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
def create_quiz(
    body: QuizIn, db: Session = Depends(get_db), teacher: User = Depends(current_teacher)
) -> Quiz:
    quiz = Quiz(title=body.title, owner_id=teacher.id)
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    return quiz


@router.get("/{quiz_id}", response_model=QuizOut)
def get_quiz(
    quiz_id: int, db: Session = Depends(get_db), teacher: User = Depends(current_teacher)
) -> Quiz:
    return _own_quiz(db, quiz_id, teacher)


@router.get("/{quiz_id}/export.md", include_in_schema=False)
def export_markdown(
    quiz_id: int,
    request: Request,
    answers: bool = True,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The quiz as Markdown, for posting as study material."""
    quiz = _own_quiz(db, quiz_id, teacher)
    body = export.as_markdown(quiz, public_base_url(request, settings), answers)
    return _download(body, "text/markdown", _filename(quiz.title, "md"))


@router.get("/{quiz_id}/export.html", include_in_schema=False)
def export_html(
    quiz_id: int,
    request: Request,
    answers: bool = True,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The same as HTML, which is what Canvas's editor takes as a paste."""
    quiz = _own_quiz(db, quiz_id, teacher)
    body = export.as_html(quiz, public_base_url(request, settings), answers)
    return _download(body, "text/html", _filename(quiz.title, "html"))


def _filename(title: str, suffix: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "quiz"
    return f"{slug}.{suffix}"


def _download(body: str, media_type: str, filename: str) -> Response:
    return Response(
        content=body,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{quiz_id}/questions", response_model=QuestionTeacherOut, status_code=status.HTTP_201_CREATED
)
def add_question(
    quiz_id: int,
    body: QuestionIn,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> Question:
    quiz = _own_quiz(db, quiz_id, teacher)
    question = Question(
        quiz_id=quiz.id,
        position=len(quiz.questions),
        text=body.text,
        image_url=body.image_url,
    )
    for i, c in enumerate(body.choices):
        question.choices.append(Choice(position=i, text=c.text, is_correct=c.is_correct))
    db.add(question)
    db.commit()
    db.refresh(question)
    return question


@router.put("/{quiz_id}/questions/order", response_model=list[QuestionTeacherOut])
def reorder_questions(
    quiz_id: int,
    body: QuestionOrder,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> list[Question]:
    """Set the running order of a quiz's questions.

    Declared before the `/{question_id}` routes so "order" is not read as a
    question id.
    """
    quiz = _own_quiz(db, quiz_id, teacher)
    try:
        return service.reorder_questions(db, quiz, body.question_ids)
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.put("/{quiz_id}/questions/{question_id}", response_model=QuestionTeacherOut)
def edit_question(
    quiz_id: int,
    question_id: int,
    body: QuestionEdit,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> Question:
    quiz = _own_quiz(db, quiz_id, teacher)
    question = db.get(Question, question_id)
    if question is None or question.quiz_id != quiz.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    try:
        return service.update_question(
            db, question, body.text, body.image_url, body.choices
        )
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))


@router.delete("/{quiz_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    quiz_id: int,
    question_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> None:
    quiz = _own_quiz(db, quiz_id, teacher)
    question = db.get(Question, question_id)
    if question is None or question.quiz_id != quiz.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    try:
        service.delete_question(db, question)
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
