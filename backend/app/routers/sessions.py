import asyncio
import csv
import io
import json

import qrcode
import qrcode.constants
import qrcode.image.svg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from .. import service
from ..auth import current_teacher, current_user
from ..config import Settings, get_settings
from ..db import SessionLocal, get_db, writing
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
from .reports import CANVAS_STUDENT_COLUMNS, canvas_student_cells

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


def _state_snapshot(session_code: str) -> dict | None:
    """Read the session state with its own short-lived database session."""
    db = SessionLocal()
    try:
        session = db.scalar(select(QuizSession).where(QuizSession.code == session_code))
        if session is None:
            return None
        return _state(db, session, user=None).model_dump(mode="json")
    finally:
        db.close()


async def _broadcast_state(session_code: str) -> None:
    """Publish the (user-independent) session state to all SSE subscribers.

    The read happens in a thread and only the publish happens here. It used to
    query directly, and that was a deadlock: this runs from `async def`
    endpoints, so a synchronous query executes *on the event loop*, and since
    every SQLite transaction now opens with `BEGIN IMMEDIATE` it may have to
    wait for the write lock. Whoever holds that lock is a worker thread that
    needs the event loop to finish its response — so neither can proceed, and
    the app unsticks only when `busy_timeout` expires fifteen seconds later.

    That is almost certainly what a 728-second request looked like from the
    inside. Nothing synchronous may touch the database from a coroutine here.
    """
    payload = await run_in_threadpool(_state_snapshot, session_code)
    if payload is not None:
        await broadcaster.publish(session_code, payload)


# --- teacher endpoints -----------------------------------------------------


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    quiz_id: int,
    loadtest: bool = False,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> QuizSession:
    """Start a lecture run of a quiz.

    `loadtest=true` marks it a rehearsal rather than a lecture, which keeps it
    out of every attendance report — see `QuizSession.is_loadtest`. It is worth
    passing for any run that is not a real class, because nothing in the app
    deletes a session afterwards except `DELETE /api/sessions/{code}`, which
    refuses anything but a rehearsal.
    """
    quiz = db.get(Quiz, quiz_id)
    if quiz is None or quiz.owner_id != teacher.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Quiz not found")
    with writing(db):
        session = QuizSession(quiz_id=quiz.id, is_loadtest=loadtest)
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


@router.delete("/{code}")
def delete_loadtest_session(
    code: str,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> dict:
    """Remove a rehearsal and everything it wrote.

    The counterpart to `loadtest=true`, and the only way anything deletes a
    session. It refuses a real one outright — answers are the one
    irreplaceable thing in this app, and an endpoint that could take a
    lecture's away by mistyping a six-character code would be worth more harm
    than it saves. A rehearsal's rows were never worth anything, which is
    exactly why they are safe to remove and worth removing: the throwaway
    students it signed in are otherwise permanent residents of a database that
    holds a real class.

    Cascades through the session's rounds to their answers, then drops the
    participant rows, then the throwaway students left with nothing to their
    name. A student with answers in some *other* session is kept, which cannot
    happen for a real student here but is checked rather than assumed.
    """
    session = _owned_session(db, code, teacher)
    if not session.is_loadtest:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This is a real session. Only a load-test session can be deleted, "
            "because its answers are the only ones that were never worth keeping.",
        )
    removed = service.delete_loadtest_session(db, session)
    return removed


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
    """Open a bout. The database work runs in a thread, never on the loop.

    See `_broadcast_state` for what happens when it does not: this endpoint
    takes SQLite's write lock and then awaits a broadcast that wants the same
    lock, while the thread holding it waits for the event loop this coroutine
    is sitting on.
    """
    round_ = await run_in_threadpool(_open_round_sync, db, code, body, teacher)
    await _broadcast_state(code)
    return round_


def _open_round_sync(
    db: Session, code: str, body: OpenRoundIn, teacher: User
) -> RoundOut:
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
    # Serialised here rather than returned as an ORM object: FastAPI would
    # otherwise read its attributes on the event loop, which can lazy-load.
    out = RoundOut.model_validate(round_)
    # End the transaction before returning. The caller broadcasts next, which
    # needs a connection of its own, and a request still holding SQLite's
    # write lock would be waiting for itself — the rule `current_user`
    # already follows: never hold a transaction across a boundary.
    db.commit()
    return out


@router.post("/{code}/rounds/{round_id}/close", response_model=RoundOut)
async def close_round(
    code: str,
    round_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
):
    """Halt a bout — in a thread, for the reason given on `open_round`."""
    round_ = await run_in_threadpool(_close_round_sync, db, code, round_id, teacher)
    await _broadcast_state(code)
    return round_


def _close_round_sync(
    db: Session, code: str, round_id: int, teacher: User
) -> RoundOut:
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
    out = RoundOut.model_validate(round_)
    # End the transaction before returning. The caller broadcasts next, which
    # needs a connection of its own, and a request still holding SQLite's
    # write lock would be waiting for itself — the rule `current_user`
    # already follows: never hold a transaction across a boundary.
    db.commit()
    return out


@router.delete("/{code}/questions/{question_id}/rounds")
async def reset_question(
    code: str,
    question_id: int,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> dict:
    """Discard this question's rounds so it can be run again.

    Destructive — it deletes the answers students already gave for this
    question in this session. Offered because a question can otherwise be
    asked only once per session, which makes rehearsing awkward.
    """
    removed = await run_in_threadpool(_reset_question_sync, db, code, question_id, teacher)
    await _broadcast_state(code)
    return {"removed_rounds": removed}


def _reset_question_sync(db: Session, code: str, question_id: int, teacher: User) -> int:
    """In a thread, for the reason given on `open_round`."""
    session = _owned_session(db, code, teacher)
    question = db.get(Question, question_id)
    if question is None or question.quiz_id != session.quiz_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    removed = service.reset_question(db, session, question)
    # End the transaction before returning. The caller broadcasts next, which
    # needs a connection of its own, and a request still holding SQLite's
    # write lock would be waiting for itself — the rule `current_user`
    # already follows: never hold a transaction across a boundary.
    db.commit()
    return removed


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


@router.get("/{code}/canvas-participation.csv", include_in_schema=False)
def session_canvas_participation_csv(
    code: str,
    assignment: str | None = None,
    threshold: float = Query(service.DEFAULT_ANSWER_THRESHOLD, ge=0.0, le=1.0),
    course_id: int | None = None,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> Response:
    """This one lecture's attendance, in Canvas's gradebook-import format.

    One point, scored on the same bar as the end-of-term file: the student
    answered at least `threshold` of this session's bouts. It goes into Canvas
    as its own assignment, so the column is named for the lecture and its date
    by default — two runs of the same quiz would otherwise land in one column.

    Personal data, like every other view of who did what: teacher-only, and
    restricted to the session's own owner.
    """
    session = _owned_session(db, code, teacher)
    course = course_id or settings.canvas_course_id
    report = service.session_canvas_participation(db, session, course, threshold)
    column = assignment or f"{report['title']} {report['date']}"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CANVAS_STUDENT_COLUMNS + [column])
    writer.writerow(
        ["    Points Possible"] + [""] * (len(CANVAS_STUDENT_COLUMNS) - 1) + [1]
    )
    for row in report["students"]:
        writer.writerow(canvas_student_cells(row) + [row["attended"]])

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="canvas-participation-{code}.csv"'
            ),
            "Cache-Control": "no-store",
        },
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


@router.get("/{code}/questions/{question_id}/discussants")
def discussants(
    code: str,
    question_id: int,
    count: int = 2,
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> dict:
    """Two students, at random, to say how they reasoned.

    The one place besides the Participants view where individuals are named,
    and the only one meant to be *projected* — so it is worth being exact
    about what it discloses. It returns who answered, never what they
    answered: the names go under the whole distribution rather than beside a
    bar, so being drawn says nothing about which choice a student picked.

    Drawn on request rather than automatically, so the teacher decides when
    names appear on a screen a lecture hall is looking at, and can draw again
    if someone is absent.
    """
    session = _owned_session(db, code, teacher)
    question = db.get(Question, question_id)
    if question is None or question.quiz_id != session.quiz_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    drawn = service.draw_discussants(db, session, question, max(1, min(count, 5)))
    # `reel` is only for the spin the projected view plays before the names
    # settle: it comes from everyone who joined, so it says nothing about who
    # answered. The draw itself is `names`.
    names = [u.display_name for u in drawn]
    return {"names": names, "reel": service.reel_names(db, session, include=names)}


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
def submit_answer(
    code: str,
    body: AnswerIn,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    """Record one student's answer.

    Deliberately a plain `def`. SQLAlchemy here is synchronous, so an
    `async def` runs those queries *on the event loop* — and this is the one
    endpoint a whole class calls at the same moment. Every answer then blocks
    every other request the process is serving, including the SSE keep-alives
    and the teacher's own screen, for as long as SQLite takes to commit. As a
    `def` FastAPI runs it in the thread pool, where the waiting is bounded and
    concurrent. It broadcasts nothing, so there is nothing here that needs to
    be awaited.
    """
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


def _join_session(code: str, user_id: int) -> str:
    """Find the session, note that the user is following it, return its code.

    Opens and closes its own Session so it borrows a pooled connection only
    while it is actually running — see `events()` for why that matters.
    """
    db = SessionLocal()
    try:
        session = db.scalar(select(QuizSession).where(QuizSession.code == code))
        if session is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        user = db.get(User, user_id)
        if user is not None and user.id != session.quiz.owner_id:
            service.record_participant(db, session, user)
        return session.code
    finally:
        db.close()


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
    # Release the request's database session before this function awaits
    # anything at all — not merely before it starts streaming.
    #
    # Two reasons, both learned the hard way. FastAPI holds a `yield`
    # dependency open until the response *completes*, and an SSE response
    # completes only when the client goes away, so `get_db`'s session would
    # otherwise live as long as the stream; a Session with an open transaction
    # keeps a pooled connection checked out for all of it. The student path
    # escaped that by accident, because `record_participant` commits and a
    # commit returns the connection, while the teacher path skipped the
    # commit — so every reconnect of the projected browser leaked one
    # connection permanently and the app froze mid-question, with /health,
    # which needs no database, still answering.
    #
    # The second reason is why the close moved *above* the join. By the time
    # this body runs, `current_user` has already run a SELECT on this session,
    # so it holds a connection. Await anything while that is true and the
    # connection is pinned for the duration of the wait — and a class scanning
    # the QR code opens hundreds of these streams at once, all waiting
    # together. Connections were held by requests that were doing nothing,
    # outnumbering the threads actually working, and the pool ran dry again
    # from the opposite direction. Nothing below needs the request's session:
    # `_join_session` takes its own for the moment it runs.
    user_id = user.id
    db.close()

    # Off the event loop: this handler must be `async def` because it returns
    # a streaming response, but the work below is synchronous SQLAlchemy and
    # one of its steps is a *write*. Run on the loop, a class arriving
    # together would take it in turns to stall every other request in the
    # process — and arriving together is what a class does.
    session_code = await run_in_threadpool(_join_session, code, user_id)

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
