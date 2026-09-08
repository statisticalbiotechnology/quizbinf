"""What keeps a lecture hall from taking the app down.

The app has frozen in front of a class once already, and the cause was never
visible in a test: every rule it broke is about what happens when 150 people
do the same thing in the same second, and a test suite does one thing at a
time. `loadtest/lecture.py` is what actually found these — it drove a whole
lecture and watched the connection pool empty. This file is what stops them
coming back, by pinning each property as a statement about the code rather
than as a measurement, so it holds on a laptop and in CI.

None of these are performance assertions. Timing on shared CI hardware proves
nothing, and a test that fails when the machine is busy would be deleted
within a month.
"""

import asyncio
import inspect

from sqlalchemy import text

from app import main
from app.auth import COOKIE_NAME, current_user
from app.config import get_settings
from app.db import SQLITE_POOL_SIZE, SessionLocal, engine, journal_mode
from app.routers import sessions
from tests.conftest import login, make_quiz_with_question


class _RequestWithCookie:
    """The only part of a Request that `current_user` reads."""

    def __init__(self, session_cookie: str) -> None:
        self.cookies = {COOKIE_NAME: session_cookie}
        self.state = type("State", (), {})()


def test_the_pool_is_larger_than_the_number_of_requests_allowed_in():
    """So running out of connections is impossible rather than merely unlikely.

    Both failures this guards against were pool exhaustion: first a leak, then
    a burst. Sizing the pool above the concurrency cap makes the queue form at
    the door — where waiting is all that happens — instead of at the pool,
    where waiting turns into a 500 for a student who has already submitted.
    """
    assert SQLITE_POOL_SIZE > main.REQUEST_SLOTS


def test_sqlite_runs_in_wal_mode():
    """Without it a single writer blocks every reader.

    One student's answer would stall the whole room's `/state` and the
    teacher's live count with it — which is the shape of "the app is slow"
    that a class actually reports.
    """
    assert journal_mode() == "wal"


def test_answering_does_not_run_on_the_event_loop():
    """`submit_answer` must stay a plain `def`.

    SQLAlchemy here is synchronous, so an `async def` would run every query on
    the event loop — and this is the one endpoint a whole class calls at the
    same moment, so each answer would block every other request the process is
    serving. It is one keyword, it looks like an improvement, and it is the
    single most expensive change anyone could make to this file.
    """
    assert not inspect.iscoroutinefunction(sessions.submit_answer), (
        "submit_answer must be a sync def so FastAPI runs it in the thread "
        "pool; as an async def it blocks the event loop for the whole class"
    )


def test_authenticating_leaves_no_transaction_open(client):
    """Auth must hand its connection back before the endpoint is scheduled.

    `current_user` runs a SELECT, and dependencies and endpoints are two
    separate hops through the thread pool. If the transaction it opens is
    still there when it returns, every request in flight holds a connection
    while it waits for a thread — so a class opening the app together starves
    itself, with most of the connections held by requests that are doing
    nothing at all. That is what emptied a pool of fifty with only forty
    threads able to work.

    Called directly, like the SSE test next door and for the same reason: what
    must hold is a property of the function — that it returns with its session
    idle — and it is invisible through the test client, which only ever sees
    the request after every hop has completed and `get_db` has cleaned up.
    """
    login(client, "anna")
    cookie = client.cookies[COOKIE_NAME]

    with SessionLocal() as db:
        user = current_user(
            request=_RequestWithCookie(cookie), db=db, settings=get_settings()
        )
        assert user.username == "anna"
        assert not db.in_transaction(), (
            "current_user left a transaction open, so every request holds a "
            "connection while it waits for a thread"
        )
        # The attributes an endpoint reads must survive the release, or this
        # trades a connection leak for a query on every attribute access.
        assert user.display_name and user.role is not None


def test_health_answers_without_touching_the_database(client):
    """It is the one thing that still works when the database does not.

    That is the whole point of it: the freeze it reports on is invisible from
    outside, because requests simply stop being answered. If `/api/health`
    needed a connection it would be the first casualty of the failure it
    exists to diagnose — which is why the journal mode it reports is read once
    at startup and cached.
    """
    # Hold every connection the pool can give out — overflow included, or the
    # pool is merely busy and this proves nothing — exactly as a stuck app
    # does. Anything that needs the database now blocks for `pool_timeout`.
    capacity = engine.pool.size() + engine.pool._max_overflow
    held = []
    try:
        while len(held) < capacity:
            session = SessionLocal()
            session.execute(text("SELECT 1"))
            held.append(session)
        assert engine.pool.checkedout() == capacity

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["db_pool"]["checked_out"] == capacity, (
            "health must report the exhaustion it is being asked about"
        )
    finally:
        for session in held:
            session.close()


def test_health_reports_the_pool_so_a_freeze_can_be_seen(client):
    """`checked_out` pinned at `size` is what the freeze looks like, and there
    was previously no way to observe it short of reading a traceback that only
    appears once the request finally times out."""
    payload = client.get("/api/health").json()
    assert set(payload["db_pool"]) >= {"size", "checked_out"}
    assert payload["journal_mode"] == "wal"


def test_a_busy_server_says_busy_rather_than_broken(monkeypatch, client):
    """A refused request must be a 503 with `Retry-After`, not a 500.

    The difference is not cosmetic. 503 says "ask again", which is true and
    which a client may act on; a 500 says the server is broken, which sends a
    teacher looking for a bug mid-lecture and tells the student nothing useful.
    """
    monkeypatch.setattr(main, "QUEUE_SECONDS", 0.01)
    monkeypatch.setattr(main, "_slots", asyncio.Semaphore(0))  # nothing may in

    response = client.get("/api/auth/me")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"


def test_the_event_stream_is_not_counted_against_the_limit():
    """Every student holds one for the whole lecture.

    Counting them would spend the entire allowance on streams that are doing
    nothing — the app would wedge itself solid partway through a class, which
    is a worse failure than the one the limit is there to prevent.
    """
    assert main._holds_no_connection("/api/sessions/abc123/events")
    assert main._holds_no_connection("/api/health")
    assert not main._holds_no_connection("/api/sessions/abc123/answers")


def test_answers_still_arrive_when_many_are_sent_at_once(teacher_client, make_client):
    """The rules survive the concurrency, which is the point of all the above.

    A modest number here — this asserts correctness under overlap, not speed:
    every answer is recorded exactly once, and one answer per student per
    round still holds when they arrive together rather than in turn.
    """
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    round_ = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    ).json()

    students = []
    for i in range(12):
        client = make_client()
        login(client, f"burst{i}")
        students.append(client)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        results = list(
            pool.map(
                lambda pair: pair[1].post(
                    f"/api/sessions/{code}/answers",
                    json={"choice_id": choice_ids[pair[0] % len(choice_ids)]},
                ),
                enumerate(students),
            )
        )

    assert [r.status_code for r in results] == [200] * 12
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")
    histogram = teacher_client.get(
        f"/api/sessions/{code}/rounds/{round_['id']}/histogram"
    ).json()
    assert histogram["total"] == 12, "an answer was lost or double-counted under load"
