"""The database engine, and the settings that decide how it behaves in a hall.

The defaults SQLAlchemy and SQLite ship with are built for a script talking to
a file, not for 150 phones answering at once. Three of them were what made the
app slow in front of a class, and each is corrected below with the reason.
`loadtest/lecture.py` reproduces the failure they caused.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


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

#: How long SQLite waits for another writer to finish before raising "database
#: is locked". Writes here are single-row and quick; the queue is what takes
#: time when a whole class answers at once.
SQLITE_BUSY_TIMEOUT_MS = 15_000


def _make_engine():
    settings = get_settings()
    url = settings.resolved_database_url
    kwargs = {"pool_timeout": POOL_TIMEOUT}
    sqlite = url.startswith("sqlite")
    if sqlite:
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["pool_size"] = SQLITE_POOL_SIZE
        kwargs["max_overflow"] = 10
    engine = create_engine(url, **kwargs)
    if sqlite:

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
