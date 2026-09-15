"""Answering "why is one request taking a minute?" from outside the container.

The app froze in front of a class, and every explanation so far has been a
concurrency story: a leaked connection, a pool too small, a cap in the wrong
place. Each was real and each is fixed. None of them was this.

What the deployment's own log finally showed is a single request holding a
slot for 31 seconds with **one** request in flight and four connections
checked out, and another for 59 seconds with *none* in flight. There is no
queue in that. One indexed query against a small table took a minute on an
idle app, which is not something the application can cause — so the guards
built so far are treating a symptom.

This module measures the layer underneath: the volume, and SQLite's state on
it. It is deliberately paranoid about its own cost, because it runs on a
deployment that is already unwell.

Two things worth knowing about how it is reached:

* It needs **no database and no login**. Authentication reads the users
  table, which is exactly what is suspected of being slow — an endpoint that
  authenticates cannot report on a database that will not answer. It is
  gated on a configured key instead, compared in constant time.
* It reports timings, file sizes and stack traces. No student data, no
  configuration values, nothing from any table.
"""

import faulthandler
import os
import sqlite3
import sys
import time
from pathlib import Path

from sqlalchemy import text

from .backup import integrity_report
from .config import Settings


def _timed(what) -> dict:
    """Run `what`, and report how long it took or how it failed."""
    started = time.perf_counter()
    try:
        result = what()
    except Exception as e:  # noqa: BLE001 — the failure is the measurement
        return {"seconds": round(time.perf_counter() - started, 3), "error": repr(e)}
    out = {"seconds": round(time.perf_counter() - started, 3)}
    if result is not None:
        out["result"] = result
    return out


def _sqlite_files(settings: Settings) -> dict:
    """Sizes of the database and its write-ahead log.

    The WAL is the interesting one. It is checkpointed back into the database
    when the last reader finishes, so a WAL that has grown to many times the
    database means checkpointing has been starved — and every read then has to
    search that WAL before it can answer, which turns a small indexed query
    into a slow one without anything in the application changing.
    """
    url = settings.resolved_database_url
    if not url.startswith("sqlite"):
        return {"note": "not a SQLite deployment"}
    db = Path(url.split("sqlite:///", 1)[-1])
    sizes = {}
    for label, path in (
        ("database", db),
        ("wal", db.with_name(db.name + "-wal")),
        ("shm", db.with_name(db.name + "-shm")),
    ):
        try:
            sizes[label] = path.stat().st_size
        except OSError as e:
            sizes[label] = f"unreadable: {e!r}"
    return sizes


def _volume_free(settings: Settings) -> dict:
    try:
        stat = os.statvfs(settings.data_dir)
    except OSError as e:  # pragma: no cover - platform-dependent
        return {"error": repr(e)}
    return {
        "free_bytes": stat.f_bavail * stat.f_frsize,
        "total_bytes": stat.f_blocks * stat.f_frsize,
    }


def storage_report(settings: Settings) -> dict:
    """Time the things a single request depends on, one at a time.

    Separated deliberately. "The app is slow" is not actionable; "an fsync on
    this volume takes eleven seconds while a query against a copy of the same
    database in /tmp takes a millisecond" is.
    """
    data = Path(settings.data_dir)
    scratch = data / f".probe-{os.getpid()}"

    def write_and_sync() -> None:
        # One small write and one fsync — what committing a single answer
        # costs, with none of SQLite's machinery in the way. If this is slow,
        # nothing above it can be fast.
        fd = os.open(scratch, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"probe")
            os.fsync(fd)
        finally:
            os.close(fd)

    def read_back() -> int:
        return len(scratch.read_bytes())

    def query() -> str:
        # A fresh read-only connection, so this measures the database rather
        # than whatever the pool's connections are currently caught up in.
        url = settings.resolved_database_url
        if not url.startswith("sqlite"):
            return "skipped: not SQLite"
        path = url.split("sqlite:///", 1)[-1]
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            connection.execute("SELECT count(*) FROM sessions").fetchone()
            return connection.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            connection.close()

    report = {
        "write_and_fsync": _timed(write_and_sync),
        "read_back": _timed(read_back),
        "sqlite_read": _timed(query),
        # What one transaction costs before it has written anything.
        #
        # The deployment's log shows single requests holding a slot for five,
        # thirty, even sixty seconds with *nothing else in flight* and the
        # connection pool almost empty. There is no queue in that, so every
        # concurrency guard in this app is beside the point for it — the time
        # is going somewhere underneath.
        #
        # `BEGIN IMMEDIATE` then `COMMIT` takes SQLite's write lock and gives
        # it straight back, writing no data. On a local disk that is
        # microseconds; over a network filesystem it is round trips, and it is
        # paid once per write — so a class of 150 answering multiplies it by
        # 150. Measuring it is the difference between "writes are slow" and a
        # number you can multiply.
        "empty_write_txn": _timed(lambda: _time_empty_transaction(settings)),
        # The unit everything else is built from. An answer submission runs
        # five queries, so across a network it cannot beat five of these.
        "round_trip": _timed(lambda: _time_round_trip(settings)),
        # What a checkpoint costs, which is the leading suspect for a stall on
        # an idle app. A checkpoint copies the write-ahead log back into the
        # database and runs *inside* whichever ordinary request trips the
        # threshold — so one student's `/state` pays for all of it. The WAL has
        # been 3.6-4.4 MB every time we have looked, which is exactly SQLite's
        # default `wal_autocheckpoint` of 1000 pages, so they fire regularly.
        #
        # PASSIVE, so it never blocks a reader or a writer: this must measure
        # the deployment, not disturb it.
        "wal_checkpoint": _timed(lambda: _time_checkpoint(settings)),
        # Is the file itself sound? Everything else here measures how *fast*
        # the storage is, and for three rounds of diagnosis that framing was
        # the mistake: requests were failing in ways that read as contention —
        # some logins 500ing in 20 ms and others not, the same count of them
        # twice — while the actual answer was that the database was damaged
        # and no amount of concurrency work would touch it. A slow disk and a
        # corrupt file look identical from a latency column and nothing else
        # the app exposed could tell them apart.
        "integrity": _timed(lambda: integrity_report(settings)),
        "files": _sqlite_files(settings),
        "volume": _volume_free(settings),
    }
    try:
        scratch.unlink()
    except OSError:
        pass
    return report


def _time_empty_transaction(settings: Settings) -> str:
    """Open a transaction and close it again, writing nothing.

    On SQLite this is `BEGIN IMMEDIATE` then `COMMIT`: the price of taking the
    write lock, separated from the price of writing anything.

    On a database across a network it is the more important number, because
    every statement is a round trip. One answer submission runs five queries —
    the user, the session, the open round, the choice, then the answer's own
    read and insert — so whatever a single round trip costs gets multiplied by
    five before a student sees their answer land. Measuring it turns "answers
    are slow" into either "network latency, so send fewer queries" or
    "something else, keep looking".
    """
    url = settings.resolved_database_url
    if url.startswith("sqlite"):
        path = url.split("sqlite:///", 1)[-1]
        connection = sqlite3.connect(path, timeout=20, isolation_level=None)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("COMMIT")
        finally:
            connection.close()
        return "lock taken and released, no rows written"

    # Through the app's own pool, so this is what a request actually pays:
    # a checked-out connection, not a fresh connect and TLS handshake.
    from .db import engine

    with engine.begin() as connection:
        connection.execute(text("SELECT 1"))
    return "one transaction through the pool, no rows written"


def _time_round_trip(settings: Settings) -> str:
    """The cost of one statement, which is the unit everything else is built from.

    Deliberately the most trivial query there is, run several times on a
    pooled connection and reported as the fastest — the floor, with no work in
    it at all. Against a local file that is microseconds; across a network it
    is the latency to the database host, and an endpoint doing five queries
    cannot be faster than five of these however well it is written.
    """
    from .db import engine

    best = None
    with engine.connect() as connection:
        for _ in range(5):
            started = time.perf_counter()
            connection.execute(text("SELECT 1"))
            elapsed = time.perf_counter() - started
            best = elapsed if best is None else min(best, elapsed)
    return f"{best * 1000:.2f} ms for one statement on a pooled connection"


def _time_checkpoint(settings: Settings) -> dict:
    """Checkpoint the write-ahead log, and report how much moved."""
    url = settings.resolved_database_url
    if not url.startswith("sqlite"):
        return {"note": "not a SQLite deployment"}
    path = url.split("sqlite:///", 1)[-1]
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    try:
        busy, log_pages, moved = connection.execute(
            "PRAGMA wal_checkpoint(PASSIVE)"
        ).fetchone()
    finally:
        connection.close()
    return {"busy": busy, "wal_pages": log_pages, "pages_checkpointed": moved}


def dump_threads(reason: str) -> None:
    """Write every thread's stack to stderr, where the deployment's log is.

    The one question a duration cannot answer: *where* is the thread parked?
    A stack naming `os.fsync` or `sqlite3.Connection.commit` says the volume;
    one naming this application says the application.

    `faulthandler` rather than `traceback`, because it is designed to run when
    the process is in trouble: no allocation, no locks of its own, and it
    prints frames rather than values, so nothing from any table can appear in
    a log line.
    """
    print(f"--- thread dump: {reason} ---", file=sys.stderr, flush=True)
    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    print("--- end thread dump ---", file=sys.stderr, flush=True)
