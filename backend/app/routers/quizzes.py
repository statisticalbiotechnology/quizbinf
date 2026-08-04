from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import service
from ..auth import current_teacher
from ..db import get_db
from ..models import Choice, Question, Quiz, User
from ..schemas import QuestionIn, QuestionTeacherOut, QuizIn, QuizOut

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
