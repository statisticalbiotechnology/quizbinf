import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from .. import service
from ..auth import current_teacher, current_user
from ..config import Settings, get_settings
from ..db import SessionLocal, get_db
from ..events import broadcaster
from ..models import Answer, Choice, Phase, Question, Quiz, QuizSession, User
from ..schemas import (
    AnswerIn,
    ComparisonOut,
    HistogramOut,
    OpenRoundIn,
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
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """The URL the projected QR code should encode."""
    session = _session_by_code(db, code)
    return {"url": f"{settings.public_base_url.rstrip('/')}/s/{session.code}"}


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
    return _state(db, _session_by_code(db, code), user)


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
