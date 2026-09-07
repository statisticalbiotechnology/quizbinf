"""An open SSE stream must not hold a database connection.

This is the bug that froze a live lecture. FastAPI keeps a `yield` dependency
open until the response *completes*, and an SSE response completes only when
the client disconnects — so `get_db`'s Session lived for the whole stream. A
Session with an open transaction holds a pooled connection, and the pool holds
`pool_size` + `max_overflow` = 15. The teacher's projected browser reconnects
over the course of a lecture; on the fifteenth, every request that touched the
database blocked on checkout and the app stopped answering mid-question, while
/health and /metrics carried on because they need no database.

The endpoint is called directly rather than over HTTP: what must hold is that
the session is released *before* the streaming response is returned, which is
a property of the function. Driving a never-ending SSE response through the
test client to observe it only adds ways for the test itself to hang.
"""

import asyncio

from app.db import SessionLocal, engine
from app.models import QuizSession, User
from app.routers.sessions import events
from tests.conftest import login, make_quiz_with_question


class _Request:
    """Only ever touched inside the stream body, which these tests do not run."""

    async def is_disconnected(self) -> bool:
        return True


def _open_stream(db, code: str, user: User) -> None:
    """Call the endpoint the way FastAPI would, and drop the response."""
    asyncio.run(events(code=code, request=_Request(), db=db, user=user))


def _session_for(teacher_client) -> str:
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    return teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]


def test_a_students_stream_releases_the_connection(teacher_client, make_client):
    code = _session_for(teacher_client)
    student = make_client()
    login(student, "anna")

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == "anna").one()
        before = engine.pool.checkedout()
        _open_stream(db, code, user)

        assert not db.in_transaction(), "the stream left a transaction open"
        # Not equality: the fix *releases* the connection this test's own
        # lookup checked out, so the count legitimately drops below the
        # baseline. What must never happen is the stream holding one.
        assert engine.pool.checkedout() <= before, "the stream holds a connection"


def test_the_owners_stream_releases_it_too(teacher_client):
    """The path that actually broke.

    A student's stream escaped by accident: `record_participant` commits, and
    a commit returns the connection. The owner skips that call, so nothing
    ended the transaction the session lookup had started — and the projected
    browser is the one that reconnects all lecture.
    """
    code = _session_for(teacher_client)

    with SessionLocal() as db:
        quiz_session = db.query(QuizSession).filter(QuizSession.code == code).one()
        owner = db.get(User, quiz_session.quiz.owner_id)
        before = engine.pool.checkedout()
        _open_stream(db, code, owner)

        assert not db.in_transaction(), "the owner's stream left a transaction open"
        assert engine.pool.checkedout() <= before, "the stream holds a connection"


def test_a_lecture_worth_of_reconnects_does_not_exhaust_the_pool(teacher_client):
    """Twice the pool's capacity, which is what a lecture accumulated.

    The sessions are deliberately kept open across the loop: that is what
    FastAPI does for the duration of each stream, and closing them per
    iteration would release the connections on the test's behalf and hide the
    leak. The pool is checked after every stream rather than only at the end,
    so a regression fails on the first leaked connection instead of blocking
    for `pool_timeout` on the fifteenth.
    """
    from sqlalchemy.pool import QueuePool

    code = _session_for(teacher_client)
    capacity = 15
    if isinstance(engine.pool, QueuePool):
        capacity = engine.pool.size() + engine.pool._max_overflow

    held = []
    try:
        for i in range(capacity * 2):
            db = SessionLocal()
            held.append(db)
            quiz_session = db.query(QuizSession).filter(QuizSession.code == code).one()
            owner = db.get(User, quiz_session.quiz.owner_id)
            _open_stream(db, code, owner)
            assert engine.pool.checkedout() == 0, (
                f"stream {i + 1} of {capacity * 2} kept a connection"
            )

        # The thing the lecture needed and did not get: with more followers
        # than the pool has connections, the teacher can still drive it.
        assert teacher_client.get(f"/api/sessions/{code}/state").status_code == 200
    finally:
        for db in held:
            db.close()
