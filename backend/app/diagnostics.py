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
        "files": _sqlite_files(settings),
        "volume": _volume_free(settings),
    }
    try:
        scratch.unlink()
    except OSError:
        pass
    return report


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
