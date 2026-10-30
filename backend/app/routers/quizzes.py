import re
from pathlib import PurePosixPath

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import export, quiz_import, service
from ..auth import current_teacher
from ..config import Settings, get_settings
from ..db import get_db, writing
from ..markdown import render as render_markdown
from ..markdown import render_inline
from ..models import Choice, Kind, Question, Quiz, User
from ..public_base import public_base_url
from .images import (
    MAX_BYTES,
    SUFFIX_FOR_EXTENSION,
    ImageRejected,
    check_image,
    store_image,
)
from ..schemas import (
    QuestionEdit,
    QuestionIn,
    QuestionOrder,
    QuestionTeacherOut,
    QuizCopyIn,
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
    with writing(db):
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


@router.post("/{quiz_id}/archive", response_model=QuizOut)
def archive_quiz(
    quiz_id: int, db: Session = Depends(get_db), teacher: User = Depends(current_teacher)
) -> Quiz:
    """Put a quiz away at the end of a course. Deletes nothing; see `restore`."""
    return service.set_quiz_archived(db, _own_quiz(db, quiz_id, teacher), True)


@router.post("/{quiz_id}/restore", response_model=QuizOut)
def restore_quiz(
    quiz_id: int, db: Session = Depends(get_db), teacher: User = Depends(current_teacher)
) -> Quiz:
    return service.set_quiz_archived(db, _own_quiz(db, quiz_id, teacher), False)


@router.post("/{quiz_id}/copy", response_model=QuizOut, status_code=status.HTTP_201_CREATED)
def copy_quiz(
    quiz_id: int,
    body: QuizCopyIn,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> Quiz:
    """A new quiz with this one's questions and none of its lectures."""
    return service.copy_quiz(db, _own_quiz(db, quiz_id, teacher), teacher, body.title)


#: A Markdown quiz with its figures: generous for a lecture, small enough that
#: one request cannot be used to fill the volume.
MAX_IMPORT_FILES = 60
MAX_MARKDOWN_BYTES = 1024 * 1024
_MARKDOWN_EXTENSIONS = {".md", ".markdown", ".txt"}


@router.post("/{quiz_id}/import")
def import_questions(
    quiz_id: int,
    files: list[UploadFile] = File(...),
    dry_run: bool = Query(True),
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Add questions to a quiz from a Markdown file, with the figures it names.

    Always answers question by question: what will be added, and what is
    refused and why. With `dry_run` (the default) nothing is stored, so the
    teacher sees the verdict before anything changes; without it the accepted
    questions are appended to the quiz and the refused ones are left out. The
    format is described in `app/quiz_import.py`.

    A plain `def`, like every endpoint that talks to the database: as an
    `async def` its synchronous queries would run on the event loop and hold
    up every other request in the process while it worked.
    """
    quiz = _own_quiz(db, quiz_id, teacher)
    if len(files) > MAX_IMPORT_FILES:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"At most {MAX_IMPORT_FILES} files at once"
        )

    markdown_files = [
        f for f in files if PurePosixPath(f.filename or "").suffix.lower() in _MARKDOWN_EXTENSIONS
    ]
    if len(markdown_files) != 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose exactly one Markdown file (.md), together with any figures it uses",
        )
    raw = markdown_files[0].file.read(MAX_MARKDOWN_BYTES + 1)
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The Markdown file is too large")
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "The Markdown file must be saved as UTF-8"
        )

    # Figures by the name the Markdown uses for them. Checked now, so a broken
    # one refuses its question in the dry run rather than failing the import.
    figures: dict[str, tuple[bytes, str]] = {}
    figure_problems: dict[str, str] = {}
    for upload in files:
        if upload is markdown_files[0]:
            continue
        name = PurePosixPath(upload.filename or "").name
        data = upload.file.read(MAX_BYTES + 1)
        suffix = SUFFIX_FOR_EXTENSION.get(PurePosixPath(name).suffix.lower())
        try:
            check_image(data, suffix)
            figures[name] = (data, suffix)
        except ImageRejected as e:
            figure_problems[name] = e.detail

    parsed = quiz_import.parse(source)
    for question in parsed.questions:
        for name in list(question.images):
            base = PurePosixPath(name).name
            if base in figures:
                continue
            # Text first: a figure that did not come along is left out and can
            # be added when editing, rather than refusing the whole question.
            reason = figure_problems.get(base, "not uploaded with the file")
            question.warnings.append(
                f"Figure '{name}' left out ({reason}); add it when editing the question."
            )
            question.text = quiz_import.drop_image(question.text, name)
            question.explanation = quiz_import.drop_image(question.explanation, name)
            question.choices = [
                (quiz_import.drop_image(text, name), correct) for text, correct in question.choices
            ]
            question.images.remove(name)

    accepted = [q for q in parsed.questions if q.ok]
    used = {PurePosixPath(n).name for q in accepted for n in q.images}
    unused = sorted(set(figures) - {PurePosixPath(n).name for q in parsed.questions for n in q.images})

    imported = 0
    if not dry_run and accepted:
        try:
            stored = {name: store_image(*figures[name], settings) for name in sorted(used)}
        except ImageRejected as e:
            raise HTTPException(e.status_code, e.detail)

        def place(markdown: str) -> str:
            mapping = {n: stored[PurePosixPath(n).name] for n in quiz_import.local_images(markdown)}
            return quiz_import.rewrite_images(markdown, mapping)

        with writing(db):
            position = len(quiz.questions)
            for parsed_question in accepted:
                question = Question(
                    quiz_id=quiz.id,
                    position=position,
                    text=place(parsed_question.text),
                    kind=Kind.choice,
                    explanation=place(parsed_question.explanation),
                )
                for i, (text, correct) in enumerate(parsed_question.choices):
                    question.choices.append(
                        Choice(position=i, text=place(text), is_correct=correct)
                    )
                db.add(question)
                position += 1
            db.commit()
        imported = len(accepted)

    return {
        "title": parsed.title,
        "notes": parsed.notes,
        "unused_figures": unused,
        "dry_run": dry_run,
        "accepted": len(accepted),
        "refused": len(parsed.questions) - len(accepted),
        "imported": imported,
        "questions": [
            {
                "number": q.number,
                "heading": q.heading,
                "ok": q.ok,
                "errors": q.errors,
                "warnings": q.warnings,
                # A preview, with figures not yet stored showing as their names.
                "text_html": render_markdown(q.text),
                "choices": [
                    {"text_html": render_inline(text), "is_correct": correct}
                    for text, correct in q.choices
                ],
                "has_explanation": bool(q.explanation),
            }
            for q in parsed.questions
        ],
    }


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
    with writing(db):
        question = Question(
            quiz_id=quiz.id,
            position=len(quiz.questions),
            text=body.text,
            image_url=body.image_url,
            kind=body.kind,
            max_words=body.max_words if body.kind is Kind.text else 1,
            explanation=body.explanation.strip(),
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
            db,
            question,
            body.text,
            body.image_url,
            body.choices,
            body.kind,
            body.max_words,
            body.explanation,
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
