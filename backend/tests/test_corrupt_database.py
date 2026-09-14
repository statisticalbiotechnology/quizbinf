"""What the app does when the database file itself is damaged.

Written after a deployment answered `database disk image is malformed` from a
live file. Nothing in the app could say so. The failures reached us as fast
500s on some logins and not others — the *same* count of them across two
separate runs, which is the signature of a damaged page rather than of
contention, and which three rounds of concurrency work had been spent
explaining. Meanwhile `GET /api/backup.zip`, the one endpoint whose purpose is
to rescue the data, declined to produce anything at all: it is built on
`VACUUM INTO`, which reads every page and so is the first thing a damaged file
breaks.

So these tests damage a database on purpose and require two things of the app:
that it can *say* the file is damaged, and that it still hands over whatever
is left.
"""

import shutil
import sqlite3
from pathlib import Path

import pytest

from app import backup


def _populate(path: Path) -> None:
    connection = sqlite3.connect(path)
    with connection:
        connection.execute("CREATE TABLE answers (id INTEGER PRIMARY KEY, who TEXT)")
        connection.execute("CREATE INDEX answers_who ON answers (who)")
        connection.executemany(
            "INSERT INTO answers (who) VALUES (?)",
            [(f"student-{i:04d}",) for i in range(400)],
        )
    connection.close()


def _corrupt(path: Path) -> None:
    """Scribble over a page in the middle of the file.

    Page 1 is the header, and damaging it makes the file unopenable, which is
    a different and easier case. Damaging a page in the body is what happened
    on the deployment: the file opens, most of it reads, and the queries that
    touch the bad page fail while everything else carries on.
    """
    size = path.stat().st_size
    assert size > 8192, "need a file with pages past the header to damage"
    with path.open("r+b") as fh:
        fh.seek(size // 2)
        fh.write(b"\xde\xad\xbe\xef" * 256)


@pytest.fixture
def settings_for(tmp_path, monkeypatch):
    def build(database: Path):
        class Fake:
            resolved_database_url = f"sqlite:///{database}"
            data_dir = str(tmp_path)

        return Fake()

    return build


def test_a_healthy_database_reports_ok(tmp_path, settings_for):
    database = tmp_path / "healthy.db"
    _populate(database)
    report = backup.integrity_report(settings_for(database))
    assert report == {"ok": True, "problems": []}


def test_a_damaged_database_is_reported_as_damaged(tmp_path, settings_for):
    """The reading that reframes everything else.

    Without it, "the volume is slow", "the pool is exhausted" and "the file is
    broken" all present as the same thing from outside: requests failing while
    /api/health says ok.
    """
    database = tmp_path / "damaged.db"
    _populate(database)
    _corrupt(database)

    report = backup.integrity_report(settings_for(database))
    assert report["ok"] is False
    assert report.get("problems") or report.get("error"), report


def test_a_backup_of_a_damaged_database_still_produces_the_bytes(tmp_path, settings_for):
    """The property that was missing when it was needed.

    `VACUUM INTO` cannot copy a damaged file, and that is exactly the moment
    somebody is asking for a backup. Refusing leaves them with nothing; a raw
    copy is torn and unreliable and is what `sqlite3 .recover` salvages rows
    from. Hand over the bytes.
    """
    database = tmp_path / "damaged.db"
    _populate(database)
    _corrupt(database)

    destination = tmp_path / "snapshot.db"
    consistent = backup.snapshot_database(settings_for(database), destination)

    assert consistent is False, "a damaged database cannot yield a clean snapshot"
    assert destination.is_file()
    assert destination.stat().st_size == database.stat().st_size


def test_a_healthy_backup_says_it_is_consistent(tmp_path, settings_for):
    database = tmp_path / "healthy.db"
    _populate(database)
    destination = tmp_path / "snapshot.db"
    assert backup.snapshot_database(settings_for(database), destination) is True
    assert sqlite3.connect(destination).execute("SELECT count(*) FROM answers").fetchone() == (400,)


def test_the_archive_warns_when_its_copy_is_a_raw_one(tmp_path, settings_for):
    """A backup that cannot be trusted must not look like one that can.

    The archive is read months later by somebody restoring in a hurry. If the
    two kinds are indistinguishable, the raw one gets restored as though it
    were clean.
    """
    import zipfile

    database = tmp_path / "damaged.db"
    _populate(database)
    _corrupt(database)
    workspace = tmp_path / "work"
    workspace.mkdir()

    archive = backup.build(settings_for(database), workspace)
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        readme = bundle.read("README.txt").decode()

    assert "quizbinf.db" in names
    assert "RAW COPY" in readme
    assert ".recover" in readme, "say how to salvage it, not merely that it is damaged"


def test_a_healthy_archive_does_not_cry_wolf(tmp_path, settings_for):
    import zipfile

    database = tmp_path / "healthy.db"
    _populate(database)
    workspace = tmp_path / "work"
    workspace.mkdir()

    archive = backup.build(settings_for(database), workspace)
    with zipfile.ZipFile(archive) as bundle:
        readme = bundle.read("README.txt").decode()
        extracted = bundle.extract("quizbinf.db", workspace / "out")

    assert "RAW COPY" not in readme
    assert "VACUUM INTO" in readme
    assert sqlite3.connect(extracted).execute("PRAGMA integrity_check").fetchall() == [("ok",)]


def test_the_storage_probe_carries_the_integrity_reading(tmp_path, settings_for):
    """It has to arrive where somebody can read it from a phone.

    The probe is unauthenticated by necessity and key-gated by design, because
    checking a login reads the users table — which is exactly what a damaged
    database may refuse to do.
    """
    from app import diagnostics

    database = tmp_path / "damaged.db"
    _populate(database)
    shutil.copyfile(database, tmp_path / "spare.db")
    _corrupt(database)

    report = diagnostics.storage_report(settings_for(database))
    assert "integrity" in report
    assert report["integrity"]["result"]["ok"] is False


def test_the_probe_times_a_bare_write_transaction(tmp_path, settings_for):
    """What one transaction costs before it has written anything.

    The deployment's log shows single requests holding a slot for five, thirty
    and sixty seconds with *nothing else in flight* and the pool almost empty.
    There is no queue in that, so the concurrency guards in this app are beside
    the point for it. `BEGIN IMMEDIATE` followed by `COMMIT` isolates the cost
    of taking the write lock from the cost of writing anything — microseconds
    on a local disk, network round trips over NFS, and paid once per answer.
    """
    from app import diagnostics

    database = tmp_path / "timing.db"
    _populate(database)
    report = diagnostics.storage_report(settings_for(database))

    assert "empty_write_txn" in report
    assert "error" not in report["empty_write_txn"], report["empty_write_txn"]
    assert report["empty_write_txn"]["seconds"] >= 0


def test_the_probe_times_a_checkpoint_and_says_how_much_moved(tmp_path, settings_for):
    """The leading suspect for a stall on an idle app.

    A checkpoint copies the write-ahead log back into the database, and it runs
    *inside* whichever ordinary request trips the threshold — so one student's
    `/state` pays for all of it. `pages_checkpointed` is what turns "the app
    hung" into "it moved four megabytes over NFS while somebody waited".

    PASSIVE, because this must measure the deployment rather than disturb it:
    it never blocks a reader or a writer, and reports `busy` if it could not
    finish.
    """
    from app import diagnostics

    database = tmp_path / "checkpointing.db"
    _populate(database)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    with connection:
        connection.executemany(
            "INSERT INTO answers (who) VALUES (?)", [(f"late-{i}",) for i in range(200)]
        )
    connection.close()

    report = diagnostics.storage_report(settings_for(database))
    checkpoint = report["wal_checkpoint"]
    assert "error" not in checkpoint, checkpoint
    assert set(checkpoint["result"]) == {"busy", "wal_pages", "pages_checkpointed"}
