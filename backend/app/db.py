"""The database engine, and the settings that decide how it behaves in a hall.

The defaults SQLAlchemy and SQLite ship with are built for a script talking to
a file, not for 150 phones answering at once. Three of them were what made the
app slow in front of a class, and each is corrected below with the reason.
`loadtest/lecture.py` reproduces the failure they caused.

The centre of this module is the split between reading and writing. SQLite
allows one writer at a time, and everything here follows from taking that
seriously rather than discovering it under load:

* a **reader** opens a deferred transaction and, under WAL, never waits for
  anybody and never fails;
* a **writer** declares itself with `writing()`, which queues in this process
  and then opens the transaction with `BEGIN IMMEDIATE`.

Neither half works without the other, and both were learned from a load test
against the deployment — the second from a version of this file that had only
the first.
"""

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache, wraps

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

log = logging.getLogger("quizbinf.db")


class Base(DeclarativeBase):
    pass


#: Requests handled by a synchronous endpoint run in Starlette's thread pool,
#: which holds 40 threads. Each of them wants a connection, so a pool smaller
#: than that turns *thread pool full* into *pool exhausted* — a different and
#: much worse failure, because a request waiting on checkout has already been
#: accepted and logged nothing. Sized above the thread pool so the queue forms
#: where it is bounded and fair, never at the pool.
SQLITE_POOL_SIZE = 50

#: How long a request will wait for a connection before giving up. The default
#: is 30 s, which is far past the point where the student has reloaded the page
#: — so the wait produced neither an answer nor an error anyone could see. Fail
#: while somebody is still watching.
POOL_TIMEOUT = 10

#: How long SQLite waits for another writer before raising "database is
#: locked". With `writing()` below serialising writers inside this process,
#: this is now only the backstop for a writer from *outside* it — the Alembic
#: step at startup, a backup, a shell. It is deliberately long because that
#: contention is rare; it is not, any more, the queue a class waits in.
SQLITE_BUSY_TIMEOUT_MS = 15_000

#: How long a request will queue for the right to write before giving up.
#: Matched to `REQUEST_QUEUE_SECONDS` in `main.py`: past this the student has
#: given up, and refusing with a 503 that says "ask again" beats a 500 that
#: says the server is broken.
WRITE_QUEUE_SECONDS = 5.0


class WriteQueueTimeout(Exception):
    """Waited `WRITE_QUEUE_SECONDS` for the right to write and gave up.

    Deliberately not an `OperationalError`: the database is fine, this process
    is merely busier than it can serve, which is a 503 and an invitation to
    retry rather than a 500.
    """


#: One writer at a time, per process, queued here rather than inside SQLite.
#:
#: SQLite's own answer to contention is `busy_timeout`, and it is not a queue:
#: a blocked writer sleeps for a while and tries again, so with forty
#: contenders the winner is whoever happens to wake at the right moment.
#: Under a class arriving at once that is starvation — most writers eventually
#: exhausted the full timeout and returned 500 while others sailed through.
#: A lock hands the database to one writer at a time in something close to
#: arrival order, and a waiter on it *waits* rather than failing.
_write_gate = threading.Lock()

#: How many threads are queued for it, and how long the longest has waited.
#:
#: Both exist because of how the previous two failures had to be diagnosed.
#: The write queue is invisible from everywhere else — a request waiting here
#: is holding a slot, holding no connection, and logging nothing — so from
#: outside it looks exactly like the last failure, a saturated request cap
#: over an idle connection pool. That ambiguity cost a deployment cycle to
#: resolve. `/api/health` reports these, so the next time it happens the
#: reading names the cause instead of ruling one out.
_waiting = 0
_waiting_lock = threading.Lock()
_longest_wait = 0.0

#: Set while this thread holds the gate, and read by the `begin` handler to
#: decide between `BEGIN` and `BEGIN IMMEDIATE`. Thread-local rather than a
#: context variable because every database call in this app runs in a worker
#: thread — a synchronous endpoint, or `run_in_threadpool` from an async one.
_local = threading.local()


def _declared_writer() -> bool:
    return getattr(_local, "depth", 0) > 0


def on_undeclared_write(statement: str) -> None:
    """Called when a write is issued outside `writing()`.

    Such a statement still runs: it is in a deferred transaction, which is
    what every version of this app before `writing()` used, and which usually
    works. What it has is the failure below — SQLite may refuse the upgrade
    from reader to writer outright — so this is a bug, not a policy question.

    It logs rather than raises because the cost of the two is not symmetric.
    A missed write path that logs is a rare 500 under load; one that raises is
    a feature that never works at all, discovered in front of a class. The
    test suite replaces this function with one that raises, so CI is where a
    missed path is caught.
    """
    log.warning("write outside writing(): %s", statement.split(None, 3)[:3])


@contextmanager
def writing(db: Session) -> Iterator[Session]:
    """Declare that the block will write, and hold the write lock for it.

    Wrap the *whole* of a read-then-write sequence, not just the write. The
    reads have to see the same snapshot the write is applied to, and that is
    what an immediate transaction gives them.

    Any transaction `db` already had is committed first: a deferred one cannot
    be promoted, which is the entire problem this exists to avoid.

    Reentrant, so a service function that declares itself can be called from a
    router that already has.
    """
    if _declared_writer():
        yield db
        return

    global _waiting, _longest_wait
    with _waiting_lock:
        _waiting += 1
    started = time.perf_counter()
    try:
        got_it = _write_gate.acquire(timeout=WRITE_QUEUE_SECONDS)
    finally:
        waited = time.perf_counter() - started
        with _waiting_lock:
            _waiting -= 1
            _longest_wait = max(_longest_wait, waited)
    if not got_it:
        raise WriteQueueTimeout(f"waited {WRITE_QUEUE_SECONDS}s to write")

    _local.depth = 1
    try:
        # End whatever read transaction got us here before opening the
        # immediate one — including the SELECT `current_user` does on the way
        # in, if this session is the one it used.
        db.commit()
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        else:
            # Bodies commit their own work; this closes anything opened after
            # that, such as the `refresh` that reads a row back. Leaving it
            # open would hold both the connection and the gate.
            db.commit()
    finally:
        _local.depth = 0
        _write_gate.release()


def write_path(fn):
    """Mark a function whose whole body is one write transaction.

    The same thing as `with writing(db)` around the body, said where a reader
    of the function will see it. The reads a write path does first — the
    checks that decide whether the write is legal — belong inside it, so the
    decision and the write see one snapshot.

    Every function it wraps takes the `Session` as its first argument, which
    is the convention throughout `service.py`.
    """

    @wraps(fn)
    def wrapper(db: Session, *args, **kwargs):
        with writing(db):
            return fn(db, *args, **kwargs)

    return wrapper


def _make_engine():
    settings = get_settings()
    url = settings.resolved_database_url
    kwargs = {"pool_timeout": POOL_TIMEOUT}
    sqlite = url.startswith("sqlite")
    if sqlite:
        # `isolation_level=None` turns off pysqlite's implicit transaction
        # handling, so the `begin` handler below is what starts a transaction
        # rather than the driver silently emitting a plain BEGIN of its own.
        kwargs["connect_args"] = {"check_same_thread": False, "isolation_level": None}
        kwargs["pool_size"] = SQLITE_POOL_SIZE
        kwargs["max_overflow"] = 10
    engine = create_engine(url, **kwargs)
    if sqlite:

        @event.listens_for(engine, "begin")
        def _begin(connection):
            """Open a transaction as a reader or as a writer, never as both.

            **Writers** need `BEGIN IMMEDIATE`, and this is the fix for the
            failure that took a lecture down. Every write path here reads
            first: find the user then insert one, find the answer then update
            it, find the participant then record them. A deferred transaction
            begins as a reader and asks to become a writer at the first
            INSERT; if any other connection has committed in between, SQLite
            refuses — and refuses **immediately**, without consulting
            `busy_timeout`, because waiting there could deadlock. The
            application sees `database is locked` in under a millisecond no
            matter how patient it was told to be, and returns a 500. Measured
            on the deployment: 152 of 200 concurrent logins failed that way,
            each in about 19 ms. It never appeared locally because the
            vulnerable window is the gap between the read and the write —
            0.02 ms against a local disk, 6 ms against the mounted volume.

            **Readers must not**, and that is the correction to the first
            version of this handler, which made every transaction immediate.
            It read `current_user`'s SELECT — one per request, on every
            request in the app — as a writer, so the whole application
            serialised behind a lock only one request could hold. The next
            load test showed what that costs: 350 answers shed with 503, the
            concurrency cap saturated at 40 in flight with the connection pool
            almost idle at 41 of 50, and only 41 of 200 students able to join
            at all. The instant failures were gone and had been replaced by a
            queue nobody reached the front of.

            Under WAL a deferred reader sees the last committed state and
            waits for nothing, which is the whole reason WAL is on.
            """
            if _declared_writer():
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.exec_driver_sql("BEGIN")

        @event.listens_for(engine, "before_cursor_execute")
        def _guard_writes(connection, cursor, statement, parameters, context, many):
            """Notice a write that did not come through `writing()`.

            The split above is only as good as its call sites, and a missed
            one fails the way this module exists to prevent — rarely, under
            load, as a 500 a student sees. Cheap enough to leave on: a string
            comparison against the first word of each statement.
            """
            if _declared_writer():
                return
            verb = statement[:6].upper()
            if verb in ("INSERT", "UPDATE", "DELETE"):
                on_undeclared_write(statement)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(connection, _record):  # pragma: no cover - trivial
            cursor = connection.cursor()
            # Write-ahead logging, the important one: without it a single
            # writer blocks *every reader* for the duration of its
            # transaction, so one student's answer stalls the whole room's
            # `/state` and the teacher's `/live` alike. With it, readers see
            # the last committed state and never wait for a writer at all.
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait for a busy writer rather than failing instantly.
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            # Under WAL, NORMAL means a commit does not fsync: losing power
            # mid-lecture can cost the last few answers, but never corrupts
            # the database. The alternative costs a disk sync per answer on a
            # volume we do not control the speed of.
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine

engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def pool_stats() -> dict:
    """A snapshot of the connection pool, for `/api/health`.

    The freeze this guards against is invisible from outside: requests simply
    stop being answered while `/health` keeps saying ok, because it needs no
    database. Reporting how many connections are checked out turns that into
    something a teacher can read off a URL mid-lecture.
    """
    pool = engine.pool
    try:
        return {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
        }
    except AttributeError:  # a pool without a queue (e.g. SingletonThreadPool)
        return {}


def write_queue_stats() -> dict:
    """What the write queue looks like right now, for `/api/health`.

    `waiting` above zero for any length of time means writers are queueing for
    each other, which is this app's remaining hard limit: SQLite takes one
    writer at a time and no amount of CPU changes that. `longest_wait` is the
    high-water mark since the process started, so a lecture that felt slow can
    be asked about afterwards.

    In-memory counters, like everything else this endpoint reports — it has to
    answer while every request that touches the database is stuck.
    """
    return {"waiting": _waiting, "longest_wait": round(_longest_wait, 3)}


@lru_cache(maxsize=1)
def journal_mode() -> str | None:
    """SQLite's journal mode, or None for any other database.

    Cached, because its one caller is `/api/health` — the endpoint that has to
    keep answering while everything that touches the database is stuck. Asking
    the database each time would make the health check the first casualty of
    the very failure it exists to report.
    """
    if not get_settings().resolved_database_url.startswith("sqlite"):
        return None
    with engine.connect() as connection:
        return connection.scalar(text("PRAGMA journal_mode"))
