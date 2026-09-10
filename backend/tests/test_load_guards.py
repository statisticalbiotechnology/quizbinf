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
import time

from sqlalchemy import text

from app import main
from app.auth import COOKIE_NAME, current_user
from app.config import get_settings
from app import db as db_module
from app import service
from app.db import (
    SQLITE_POOL_SIZE,
    SessionLocal,
    WriteQueueTimeout,
    engine,
    journal_mode,
    writing,
)
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


def test_health_does_not_queue_behind_the_thread_pool():
    """It must run on the event loop, not in the thread pool.

    Exempting it from the concurrency cap was not enough: FastAPI runs a
    plain `def` endpoint in the thread pool, which is a second queue behind
    the first. On a wedged deployment — forty requests stuck holding threads —
    this endpoint took 23 seconds or timed out, at the one moment it exists
    for. Nothing in it does I/O, so nothing justifies a thread.
    """
    assert inspect.iscoroutinefunction(main.health), (
        "health must be an async def, or it waits for a thread pool that is "
        "exhausted exactly when someone needs to know why"
    )


def test_health_answers_without_touching_the_database(client):
    """It is the one thing that still works when the database does not.

    That is the whole point of it: the freeze it reports on is invisible from
    outside, because requests simply stop being answered. If `/api/health`
    needed a connection it would be the first casualty of the failure it
    exists to diagnose — which is why the journal mode it reports is read once
    at startup and cached.

    The pool is exhausted with *raw* connections rather than by running a
    query on each. Since `BEGIN IMMEDIATE`, a query starts a write
    transaction, and sixty of those cannot coexist by design — the earlier
    version of this test held sixty read transactions open, which was legal
    then and is a sixty-deep queue now. Checking a connection out without
    using it still empties the pool, which is the condition under test.
    """
    capacity = engine.pool.size() + engine.pool._max_overflow
    held = []
    try:
        while len(held) < capacity:
            held.append(engine.raw_connection())
        assert engine.pool.checkedout() == capacity

        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["db_pool"]["checked_out"] == capacity, (
            "health must report the exhaustion it is being asked about"
        )
    finally:
        for connection in held:
            connection.close()


def test_a_read_then_write_does_not_fail_instantly(teacher_client, make_client):
    """The failure that took a lecture down, and it is not about speed.

    Every endpoint here reads before it writes. SQLAlchemy opens that as a
    deferred transaction — a reader asking to become a writer at the first
    INSERT — and if another connection has committed in between, SQLite
    refuses *immediately*, without consulting `busy_timeout`, because waiting
    could deadlock. On the deployment 152 of 200 concurrent logins died that
    way, each in about 19 ms, and each one was a student who could not sign
    in. It never showed up locally, where the window between the read and the
    write is 0.02 ms rather than the 6 ms a mounted volume costs.

    `BEGIN IMMEDIATE` is what makes concurrent writers queue instead of fail.
    This drives the same shape through the app: many clients reading and then
    writing at once, all of which must succeed.
    """
    import concurrent.futures

    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    )

    students = []
    for i in range(16):
        student = make_client()
        login(student, f"racer{i}")
        students.append(student)

    def read_then_write(pair):
        index, student = pair
        # `/state` reads and records the participant; the answer reads the
        # open round and writes. Both are the read-then-write shape.
        student.get(f"/api/sessions/{code}/state")
        return student.post(
            f"/api/sessions/{code}/answers",
            json={"choice_id": choice_ids[index % len(choice_ids)]},
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(read_then_write, enumerate(students)))

    failed = [r for r in results if r.status_code != 200]
    assert not failed, (
        f"{len(failed)} of {len(results)} lost the read-to-write upgrade: "
        f"{[r.text[:80] for r in failed[:3]]}"
    )


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
    assert not main.needs_the_database("/api/sessions/abc123/events")
    assert not main.needs_the_database("/api/health")
    assert main.needs_the_database("/api/sessions/abc123/answers")


def test_the_page_itself_is_never_capped():
    """The failure that shut a class out of the app.

    The cap exists to protect the connection pool, so it may only cover
    requests that take a connection. Written as "everything except a couple of
    exemptions", it also covered the SPA's HTML and every built asset — and a
    class arriving together is over a thousand of those before a single API
    call. Students were refused the page, reloaded, and doubled the stampede.
    """
    for path in (
        "/s/qs3yw2",  # the URL in the QR code
        "/login",
        "/",
        "/main-466S722R.js",
        "/chunk-B5Z6JVCU.js",
        "/styles-ABCD1234.css",
        "/favicon.ico",
    ):
        assert not main.needs_the_database(path), f"{path} must never queue for a connection"


def test_the_spa_is_served_while_every_slot_is_taken(client, monkeypatch):
    """Not just uncounted in principle — actually served with the cap full.

    Checked end to end rather than by reading the predicate, because what
    broke was the middleware's reach, not anyone's intent.
    """
    monkeypatch.setattr(main, "QUEUE_SECONDS", 0.01)
    monkeypatch.setattr(main, "_slots", asyncio.Semaphore(0))  # nothing may in

    # An API call is refused, as designed...
    assert client.get("/api/auth/me").status_code == 503
    # ...but the way into the app is not.
    for path in ("/s/qs3yw2", "/login"):
        assert client.get(path).status_code in (200, 404), (
            f"{path} was blocked by the concurrency cap"
        )


def test_the_cap_can_be_switched_off_without_a_rebuild(monkeypatch, client):
    """`REQUEST_SLOTS=0` in the volume's config file, and a restart.

    This cap took the app down in front of a class once. A limit whose only
    remedy is building and deploying a new image is one nobody can back out of
    with students in the room, so it has to be reachable the way every other
    setting on this deployment is — a line in the file on the volume.
    """
    from app.config import Settings

    assert Settings(request_slots=0).request_slots == 0

    monkeypatch.setattr(main, "_slots", None)
    assert client.get("/api/auth/me").status_code in (200, 401)


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


def test_rejoining_does_not_write_every_time(teacher_client, make_client):
    """`/state` and the SSE connect must not each cost a write transaction.

    `last_seen_at` is read nowhere in the app, so refreshing it on every call
    bought nothing — and writes serialise, so on a deployment whose volume is
    slower than a local disk it was a real cost paid by every arrival and
    every reconnect. A rehearsal against the real host logged one login
    holding its slot for 25 seconds while a class arrived together.
    """
    from sqlalchemy import event as sa_event

    from app.db import engine
    from app.models import SessionParticipant

    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    student = make_client()
    login(student, "rejoiner")

    writes: list[str] = []

    @sa_event.listens_for(engine, "before_cursor_execute")
    def count_writes(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement.split()[0].upper())

    try:
        assert student.get(f"/api/sessions/{code}/state").status_code == 200
        first = len([w for w in writes if w == "INSERT"])
        writes.clear()
        # The same student comes back — a resync, a reconnecting phone.
        for _ in range(5):
            assert student.get(f"/api/sessions/{code}/state").status_code == 200
    finally:
        sa_event.remove(engine, "before_cursor_execute", count_writes)

    assert first == 1, "joining must still record the participant once"
    assert writes == [], f"re-joining wrote {writes} when nothing needed saying"

    # And the row is still there, so `joined` is unaffected.
    with SessionLocal() as db:
        assert db.query(SessionParticipant).count() == 1


def test_a_reader_does_not_wait_for_a_writer(teacher_client, make_client):
    """The regression the *second* load test found, and the reason for the
    read/write split in `db.py`.

    The first fix for the read-to-write upgrade made every transaction
    immediate — including the SELECT `current_user` does on the way into every
    request in the app. That took SQLite's exclusive write lock once per
    request, so the whole application serialised behind a lock only one
    request could hold at a time. Against the deployment: 350 answers shed
    with 503, the concurrency cap saturated at 40 in flight while the
    connection pool sat almost idle, and only 41 of 200 students managed to
    join at all.

    A pool with capacity to spare and a saturated request cap is the signature
    of it: the requests were not waiting for the database, they were waiting
    for each other.

    So: hold the write gate, and require a plain read to be served anyway.
    Under WAL a deferred reader sees the last committed state and waits for
    nobody, which is the whole reason WAL is on. Make `_begin` in `db.py`
    unconditional again and this test is what stops it.
    """
    import threading

    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]

    student = make_client()
    login(student, "reader")
    student.get(f"/api/sessions/{code}/state")  # so the participant row exists

    holding = threading.Event()
    release = threading.Event()

    def hold_the_write_lock():
        db = SessionLocal()
        try:
            with writing(db):
                db.execute(text("UPDATE sessions SET code = code"))
                holding.set()
                release.wait(timeout=10)
        finally:
            db.close()

    writer = threading.Thread(target=hold_the_write_lock, daemon=True)
    writer.start()
    try:
        assert holding.wait(timeout=5), "the writer never took the lock"
        started = time.perf_counter()
        resp = student.get(f"/api/sessions/{code}/state")
        took = time.perf_counter() - started
    finally:
        release.set()
        writer.join(timeout=10)

    assert resp.status_code == 200
    # Generously above anything a local read costs and far below the ten
    # seconds the writer holds the lock for: this asserts *whether* it waited,
    # not how fast the machine is.
    assert took < 2.0, f"a read waited {took:.1f}s for a writer that holds the lock"


def test_a_writer_that_cannot_get_a_turn_says_busy_rather_than_broken(monkeypatch):
    """Refusing to write is a 503, never a 500.

    The two are not interchangeable to a phone: 503 with `Retry-After` says
    "ask again", which the student view acts on by re-sending the answer with
    a jittered backoff, while a 500 says the server is broken and invites
    nobody to retry. What this replaced was SQLite exhausting its busy timeout
    and raising `database is locked`, which reached the student as a 500.
    """
    import threading

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # The setting, not the module constant: the timeout is read from
    # configuration at each call so it can be retuned on a deployment without
    # a rebuild. Patching the constant here passed anyway — by waiting the
    # full five seconds — which is a test agreeing with itself rather than
    # with the code.
    monkeypatch.setattr(get_settings(), "write_queue_seconds", 0.1)

    probe = FastAPI()
    probe.add_exception_handler(WriteQueueTimeout, main.write_queue_timeout)

    @probe.post("/write")
    def write() -> dict:
        db = SessionLocal()
        try:
            with writing(db):
                db.execute(text("UPDATE sessions SET code = code"))
            return {"ok": True}
        finally:
            db.close()

    holding = threading.Event()
    release = threading.Event()

    def hog():
        db = SessionLocal()
        try:
            with writing(db):
                holding.set()
                release.wait(timeout=10)
        finally:
            db.close()

    thread = threading.Thread(target=hog, daemon=True)
    thread.start()
    try:
        assert holding.wait(timeout=5)
        resp = TestClient(probe, raise_server_exceptions=False).post("/write")
    finally:
        release.set()
        thread.join(timeout=10)

    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After") == "1"


def test_declaring_a_write_twice_is_not_a_deadlock():
    """`writing()` is reentrant, because the call sites nest.

    A router declares a write and calls a service function that declares one
    too — `create_session` and `open_round` are both shapes of this. A
    non-reentrant lock would hang the request against itself, which is exactly
    the failure the previous round of this work was about.
    """
    db = SessionLocal()
    try:
        with writing(db):
            with writing(db):
                db.execute(text("UPDATE sessions SET code = code"))
    finally:
        db.close()


def test_reading_the_state_takes_no_write_lock(teacher_client, make_client):
    """The busiest path in the app must not declare itself a writer.

    `record_participant` runs on `/state` and on the SSE connect, so it is on
    every request a student makes, and the throttle means it writes on almost
    none of them. Wrapping the whole function in `writing()` would have been
    the natural way to satisfy the write-path rule and would have reinstated
    the serialisation this suite exists to prevent, from the busiest path
    there is.
    """
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    student = make_client()
    login(student, "settled")
    student.get(f"/api/sessions/{code}/state")  # first call creates the row

    seen = []
    original = db_module.writing

    def watched(db):
        seen.append(True)
        return original(db)

    db_module.writing = watched
    service.writing = watched
    try:
        assert student.get(f"/api/sessions/{code}/state").status_code == 200
    finally:
        db_module.writing = original
        service.writing = original
    assert not seen, "a settled student's /state asked for the write lock"


def test_health_says_which_build_is_running(client):
    """"The fix did not work" and "the fix was never deployed" look identical.

    They looked identical for most of a week. `/api/health` now carries a hash
    of the application's own source, so a load test against a deployment can
    be checked against the checkout it was supposed to be testing before its
    numbers are believed.
    """
    body = client.get("/api/health").json()
    assert body["code"] == main.CODE_FINGERPRINT
    assert len(body["code"]) == 8
    # Stable across calls: computed once at startup, not per request.
    assert client.get("/api/health").json()["code"] == body["code"]


def test_a_write_outside_writing_is_noticed():
    """The split is only as good as its call sites, so a missed one is loud.

    In production this logs and lets the statement through — it is the
    behaviour every build before `writing()` had, and breaking a feature
    outright is worse than the rare 500 it risks. In the test suite it raises,
    which is what makes CI the place a missed write path is found. That
    substitution is installed in `conftest.py` and is why every other test in
    this repository is also an assertion that its write paths are declared.
    """
    noticed = []
    original = db_module.on_undeclared_write
    db_module.on_undeclared_write = noticed.append
    db = SessionLocal()
    try:
        db.execute(text("UPDATE sessions SET code = code"))
        db.commit()
    finally:
        db_module.on_undeclared_write = original
        db.close()

    assert noticed, "an undeclared write went unnoticed"
    assert "UPDATE" in noticed[0]


def test_health_reports_the_write_queue(client):
    """The one number that tells this failure from the last one.

    A saturated request cap over an idle connection pool is what *both* looked
    like from outside: the previous fix's serialisation, and the pool
    exhaustion before it. Telling them apart took deploying a change and
    running the load test again. `waiting` says directly whether requests are
    queueing for the write lock, which is the app's remaining hard limit and
    the one a bigger machine does not raise.
    """
    queue = client.get("/api/health").json()["write_queue"]
    assert queue["waiting"] == 0
    assert queue["longest_wait"] >= 0


def test_a_returning_student_signs_in_without_the_write_lock(client, make_client):
    """The most expensive needless write in the app, and the one that hurts most.

    Every student's arrival goes through the login path, so a class arriving
    together is 150 calls inside a few seconds. Writing unconditionally — even
    re-setting `role` to the value it already held — made every one of them
    queue for the process-wide write lock. Against the deployment: 185 of 200
    logins refused after the full `WRITE_QUEUE_SECONDS`, 8 students in the
    session, and the connection pool idle at 15 of 50. Nothing was contended
    except a lock taken for no reason.

    The first login of a name creates a row and must write. Every login after
    that has nothing to write, and must not queue behind anybody.
    """
    seen = []
    original = db_module.writing

    def watched(db):
        seen.append(True)
        return original(db)

    login(client, "returning")  # creates the row — this one may write

    import app.auth as auth_module

    db_module.writing = watched
    auth_module.writing = watched
    try:
        again = make_client()
        login(again, "returning")
    finally:
        db_module.writing = original
        auth_module.writing = original

    assert not seen, "a returning student's login asked for the write lock"
    assert again.get("/api/auth/me").json()["username"] == "returning"


def test_a_changed_role_is_still_written(monkeypatch, client, make_client):
    """The saving above must not become a correctness bug.

    The teacher allowlist in configuration is authoritative on every login —
    promoting somebody by adding them to `TEACHER_USERNAMES` has to take
    effect the next time they sign in, which means the skip has to notice that
    the stored row no longer matches.
    """
    login(client, "promoted")
    assert client.get("/api/auth/me").json()["role"] == "student"

    settings = get_settings()
    monkeypatch.setattr(
        settings, "teacher_usernames", settings.teacher_usernames + ",promoted"
    )

    after = make_client()
    login(after, "promoted")
    assert after.get("/api/auth/me").json()["role"] == "teacher"


def test_the_write_timeout_can_be_retuned_without_a_rebuild(monkeypatch):
    """SQLite's one-writer limit is the app's hardest, so its timeout is
    configuration.

    The request cap already learned this: a limit whose only remedy is
    building and deploying a new image is one nobody can back out of with a
    class in the room, and that cap had to be backed out of exactly once. The
    write queue is the same shape of thing and reaches its ceiling sooner —
    `WRITE_QUEUE_SECONDS` in the volume's config file, and a restart.
    """
    settings = get_settings()
    assert db_module._write_timeout() == settings.write_queue_seconds
    monkeypatch.setattr(settings, "write_queue_seconds", 12.5)
    assert db_module._write_timeout() == 12.5
