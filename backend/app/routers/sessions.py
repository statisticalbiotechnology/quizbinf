import asyncio
import csv
import io
import json

import qrcode
import qrcode.constants
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from .. import service
from ..auth import current_teacher, current_user
from ..config import Settings, get_settings
from ..db import SessionLocal, get_db
from ..events import broadcaster
from ..models import Answer, Choice, Phase, Question, Quiz, QuizSession, User
from ..public_base import public_base_url
from ..schemas import (
    AnswerIn,
    ComparisonOut,
    HistogramOut,
    LiveCountOut,
    OpenRoundIn,
    ParticipantsOut,
    ParticipationReportOut,
    RoundOut,
    SessionOut,
    SessionState,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

KEEPALIVE_SECONDS = 15  # keeps the SSE stream alive through Serve's proxy


def _session_by_code(db: Session, code: str) -> QuizSession:
    session = db.scalar(select(QuizSession).where(QuizSession.code == code))
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


def _state(db: Session, session: QuizSession, user: User | None) -> SessionState:
    open_round = service.get_open_round(db, session)
    question = open_round.question if open_round else None
    my_choice_id = None
    if open_round and user:
        answer = db.scalar(
            select(Answer).where(
                Answer.round_id == open_round.id, Answer.user_id == user.id
            )
        )
        my_choice_id = answer.choice_id if answer else None
    return SessionState(
        code=session.code,
        # The teacher view loads the session's questions from this; matching on
        # the title instead breaks as soon as two quizzes share one.
        quiz_id=session.quiz_id,
        quiz_title=session.quiz.title,
        open_round=RoundOut.model_validate(open_round) if open_round else None,
        question=question,
        my_choice_id=my_choice_id,
    )


async def _broadcast_state(session_code: str) -> None:
    """Publish the (user-independent) session state to all SSE subscribers."""
    db = SessionLocal()
    try:
        session = db.scalar(select(QuizSession).where(QuizSession.code == session_code))
        if session is not None:
            state = _state(db, session, user=None)
            await broadcaster.publish(session_code, state.model_dump(mode="json"))
    finally:
        db.close()


# --- teacher endpoints -----------------------------------------------------


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    quiz_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> QuizSession:
    quiz = db.get(Quiz, quiz_id)
    if quiz is None or quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quiz not found")
    session = QuizSession(quiz_id=quiz.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.get("/{code}/join-url")
def join_url(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """The URL the projected QR code should encode."""
    session = _session_by_code(db, code)
    return {"url": f"{public_base_url(request, settings)}/s/{session.code}"}


@router.get("/{code}/qr.svg", include_in_schema=False)
def join_qr(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> Response:
    """The projected QR code, rendered server-side as SVG.

    Generated here rather than in the browser because this code is the only
    way students reach the app: a bundling or interop problem in a client-side
    QR library would leave the teacher projecting a broken image, which is not
    recoverable in the middle of a lecture. SVG also scales losslessly for
    projection.
    """
    session = _session_by_code(db, code)
    url = f"{public_base_url(request, settings)}/s/{session.code}"
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buf)
    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        # The session code is stable, but the derived host is not; keep it fresh.
        headers={"Cache-Control": "no-store"},
    )


@router.post("/{code}/rounds", response_model=RoundOut, status_code=status.HTTP_201_CREATED)
async def open_round(
    code: str,
    body: OpenRoundIn,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
):
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    question = db.get(Question, body.question_id)
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    try:
        round_ = service.open_round(db, session, question, body.phase)
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await _broadcast_state(session.code)
    return round_


@router.post("/{code}/rounds/{round_id}/close", response_model=RoundOut)
async def close_round(
    code: str,
    round_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
):
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    round_ = next((r for r in session.rounds if r.id == round_id), None)
    if round_ is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Round not found")
    try:
        round_ = service.close_round(db, round_)
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    await _broadcast_state(session.code)
    return round_


@router.get("/{code}/rounds/{round_id}/histogram", response_model=HistogramOut)
def histogram(
    code: str,
    round_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> HistogramOut:
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    round_ = next((r for r in session.rounds if r.id == round_id), None)
    if round_ is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Round not found")
    counts = service.round_histogram(db, round_)
    return HistogramOut(
        round_id=round_.id, phase=round_.phase, counts=counts, total=sum(counts.values())
    )


@router.get("/{code}/participants", response_model=ParticipantsOut)
def participants(
    code: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> ParticipantsOut:
    """How many students are in the session — a count, never a list of names.

    `joined` is everyone who has opened it; `connected` is how many streams are
    open right now, which drops when phones sleep, so `joined` is the number
    worth projecting.
    """
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    return ParticipantsOut(
        joined=service.participant_count(db, session),
        connected=broadcaster.connected(session.code),
    )


def _owned_session(db: Session, code: str, teacher: User) -> QuizSession:
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    return session


@router.get("/{code}/participation", response_model=ParticipationReportOut)
def participation(
    code: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> ParticipationReportOut:
    """Who answered what, per student — the one personal-data view in the app.

    Teacher-only, and only for their own session. Intended for formative use:
    seeing who is following along, not grading.
    """
    session = _owned_session(db, code, teacher)
    return ParticipationReportOut(
        questions=session.quiz.questions,
        rows=service.participation_report(db, session),
    )


@router.get("/{code}/participation.csv", include_in_schema=False)
def participation_csv(
    code: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> Response:
    """The same report as CSV, for keeping a participation record."""
    session = _owned_session(db, code, teacher)
    questions = session.quiz.questions
    rows = service.participation_report(db, session)

    def mark(value: bool | None) -> str:
        if value is None:
            return "-"
        return "correct" if value else "wrong"

    buf = io.StringIO()
    writer = csv.writer(buf)
    header = ["username", "name"]
    for i, _ in enumerate(questions, start=1):
        header += [f"q{i}_pre", f"q{i}_post"]
    header += ["answered", "pre_correct", "post_correct"]
    writer.writerow(header)
    for row in rows:
        line = [row["username"], row["display_name"]]
        for answer in row["answers"]:
            line += [mark(answer["pre"]), mark(answer["post"])]
        line += [row["answered"], row["pre_correct"], row["post_correct"]]
        writer.writerow(line)

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="quizbinf-{session.code}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/{code}/live", response_model=LiveCountOut)
def live_count(
    code: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> LiveCountOut:
    """How many answers have arrived in the currently open round.

    Deliberately a *count only*, never the per-choice breakdown: the teacher's
    screen is the projected one, and showing the distribution while the round
    is open would bias the peer discussion that follows.
    """
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    round_ = service.get_open_round(db, session)
    if round_ is None:
        return LiveCountOut(open_round=None, answered=0)
    return LiveCountOut(
        open_round=RoundOut.model_validate(round_), answered=len(round_.answers)
    )


@router.get("/{code}/questions/{question_id}/comparison", response_model=ComparisonOut)
def comparison(
    code: str,
    question_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> ComparisonOut:
    session = _session_by_code(db, code)
    if session.quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your session")
    question = db.get(Question, question_id)
    if question is None or question.quiz_id != session.quiz_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    return ComparisonOut(**service.pre_post_comparison(db, session, question))


# --- student endpoints -----------------------------------------------------


@router.get("/{code}/state", response_model=SessionState)
def session_state(
    code: str, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> SessionState:
    """Full state snapshot; clients call this on connect/reconnect to resync."""
    session = _session_by_code(db, code)
    # Opening the session is what "joining" means — the projected join screen
    # shows this count so the teacher can see the room filling up before any
    # round is open. The teacher running it is not a member of the room, and
    # their own views poll this endpoint, so exclude the owner.
    if user.id != session.quiz.owner_id:
        service.record_participant(db, session, user)
    return _state(db, session, user)


@router.post("/{code}/answers")
async def submit_answer(
    code: str,
    body: AnswerIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    session = _session_by_code(db, code)
    round_ = service.get_open_round(db, session)
    if round_ is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No round is open")
    choice = db.get(Choice, body.choice_id)
    if choice is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Choice not found")
    try:
        service.submit_answer(db, round_, user, choice)
    except service.RuleViolation as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return {"ok": True, "choice_id": choice.id}


@router.get("/{code}/events")
async def events(
    code: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """SSE stream of session-state changes.

    Clients should treat every event as a full state snapshot and additionally
    call /state after (re)connecting — events sent while disconnected are lost.
    """
    session = _session_by_code(db, code)
    session_code = session.code
    if user.id != session.quiz.owner_id:
        service.record_participant(db, session, user)

    async def stream():
        queue = broadcaster.subscribe(session_code)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                    yield {"event": "state", "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"comment": "keepalive"}
        finally:
            broadcaster.unsubscribe(session_code, queue)

    return EventSourceResponse(stream())
