"""A downloadable copy of everything on the volume that cannot be recreated.

The database is a single SQLite file with no replication, no snapshots and no
off-site copy: if the volume goes, every answer any student has ever given
goes with it. Exporting `participation.csv` after each lecture was the only
safeguard, and it only covers what the teacher remembered to download.

What goes in, and why:

* **The database.** Taken with `VACUUM INTO`, which produces a consistent
  copy without blocking writers — a plain file copy of a live SQLite database
  can land mid-transaction and restore as a corrupt file.
* **The uploaded figures.** Questions reference them by path; a database
  restored without them renders questions with broken images.
* **The configuration**, with secrets removed — see below.

What is deliberately left out: nothing else on the volume is anything but
derived state.
"""

import logging
import re
import secrets
import shutil
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

log = logging.getLogger("quizbinf.backup")

#: Any configuration key whose *value* is a credential. Matched by pattern
#: rather than listed, so a secret added later is redacted by default instead
#: of being published by an oversight — the failure direction matters more
#: than the precision here. `KEY` earns its place the hard way: `LOADTEST_KEY`
#: was added later, matched none of the other words, and rode out inside an
#: archive until a test asked. A setting named `…KEY` is a credential far more
#: often than not, and over-redacting costs a lookup.
SECRET_KEY = re.compile(r"SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|KEY", re.IGNORECASE)

#: A password embedded in a database URL, e.g. postgresql://user:pw@host/db.
URL_PASSWORD = re.compile(r"(?P<prefix>://[^:/@\s]+:)(?P<password>[^@/\s]+)(?P<at>@)")

REDACTED = "<redacted — set this on the new host>"


def redact_config(text: str) -> str:
    """Blank out every credential in an env file, keeping the keys.

    The keys are the useful part of a backup: they say what has to be
    configured on a new host. The values are obtainable again — a Canvas token
    is self-service, the OIDC secret comes from the key vault, the teacher
    password is chosen — so shipping them inside an archive that will sit in a
    downloads folder buys convenience at a poor price.
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, value = line.partition("=")
        if SECRET_KEY.search(key):
            out.append(f"{key}={REDACTED}")
        else:
            # Not a secret by name, but a URL may still carry a password.
            out.append(key + "=" + URL_PASSWORD.sub(_mask_url_password, value))
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _mask_url_password(match: re.Match) -> str:
    return match["prefix"] + REDACTED + match["at"]


class NotSupported(Exception):
    """This deployment's database cannot be snapshotted from inside the app."""


def sqlite_path(settings: Settings) -> Path:
    """The SQLite file behind this deployment, or raise for anything else."""
    url = settings.resolved_database_url
    if not url.startswith("sqlite"):
        raise NotSupported(
            "This deployment does not use SQLite, so the app cannot take its "
            "own snapshot. Use the database server's own tooling (pg_dump) "
            "instead."
        )
    return Path(url.split("sqlite:///", 1)[-1])


def snapshot_database(settings: Settings, destination: Path) -> bool:
    """Copy the database. Returns True if the copy is a consistent one.

    `VACUUM INTO` is the way to do this while a lecture is running: it reads
    through a normal transaction, so a session carries on around it, where
    copying the file byte-for-byte can catch it mid-write and produce a
    snapshot that will not open.

    But `VACUUM INTO` reads *every page*, so it is also the first thing to
    fail when the database is damaged — and a backup that refuses precisely
    when the database is broken is no backup at all. That is not hypothetical:
    this deployment returned `database disk image is malformed` from a live
    file whose real lecture data was still readable, and the endpoint that
    exists to rescue it declined to produce anything.

    So a failed VACUUM falls back to copying the bytes. What that yields is
    worse in every way except the one that matters: it may be torn, it may
    hold a half-finished transaction, and it is what `sqlite3 .recover` needs
    in order to salvage rows from a file that will no longer open. The write-
    ahead log goes with it, since under WAL the recent commits are in there
    rather than in the database file, and a copy without it silently loses
    them.

    The caller is told which kind it got, because the difference decides what
    the archive can be used for.
    """
    source = sqlite_path(settings)
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        connection.execute("VACUUM INTO ?", (str(destination),))
        return True
    except sqlite3.DatabaseError as e:
        log.warning("VACUUM INTO failed (%s); falling back to a raw copy", e)
    finally:
        connection.close()

    shutil.copyfile(source, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = source.with_name(source.name + suffix)
        if sidecar.is_file():
            shutil.copyfile(sidecar, destination.with_name(destination.name + suffix))
    return False


def integrity_report(settings: Settings, limit: int = 20) -> dict:
    """What SQLite says about the file, from a read-only connection.

    The one question that could not be asked from outside the container, and
    the one whose answer reframed everything: requests were failing in ways
    that looked like contention — fast 500s on some logins and not others,
    the same count twice — and none of it was contention. The file was
    damaged, and every guard built for concurrency was treating a symptom.

    `integrity_check` rather than `quick_check`: the cheap one skips exactly
    the index checking that turned out to matter here, and this database is
    small enough that reading all of it costs little. Bounded to `limit`
    problems, because a badly damaged file can report thousands and the first
    few say as much as all of them.
    """
    url = settings.resolved_database_url
    if not url.startswith("sqlite"):
        return {"note": "not a SQLite deployment"}
    path = url.split("sqlite:///", 1)[-1]
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as e:  # pragma: no cover - the file is unopenable
        return {"ok": False, "error": repr(e)}
    try:
        rows = connection.execute(f"PRAGMA integrity_check({int(limit)})").fetchall()
    except sqlite3.DatabaseError as e:
        # Damaged past the point of being able to say how.
        return {"ok": False, "error": repr(e)}
    finally:
        connection.close()

    problems = [row[0] for row in rows if row and row[0] != "ok"]
    return {"ok": not problems, "problems": problems}


def build(settings: Settings, workspace: Path) -> Path:
    """Assemble the archive under `workspace` and return its path."""
    taken = datetime.now(timezone.utc)
    archive = workspace / f"quizbinf-backup-{taken:%Y-%m-%d}.zip"

    # A unique name, not "quizbinf.db": `VACUUM INTO` refuses to overwrite,
    # and naming the snapshot after the source makes the two collide the
    # moment the workspace and the data directory are the same place.
    database = workspace / f"snapshot-{secrets.token_hex(8)}.db"
    consistent = snapshot_database(settings, database)

    data = Path(settings.data_dir)
    images = sorted(p for p in (data / "images").glob("*") if p.is_file())
    config = data / "quizbinf.env"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(database, "quizbinf.db")
        if not consistent:
            # The raw copy's write-ahead log holds the commits that are not in
            # the database file yet; a recovery without it silently loses them.
            for suffix in ("-wal", "-shm"):
                sidecar = database.with_name(database.name + suffix)
                if sidecar.is_file():
                    bundle.write(sidecar, f"quizbinf.db{suffix}")
        for image in images:
            bundle.write(image, f"images/{image.name}")
        if config.is_file():
            bundle.writestr(
                "quizbinf.env", redact_config(config.read_text(encoding="utf-8"))
            )
        bundle.writestr(
            "README.txt",
            _readme(
                taken,
                database.stat().st_size,
                len(images),
                config.is_file(),
                consistent,
            ),
        )

    for path in (database, *(database.with_name(database.name + s) for s in ("-wal", "-shm"))):
        path.unlink(missing_ok=True)
    return archive


def _readme(
    taken: datetime,
    db_bytes: int,
    image_count: int,
    had_config: bool,
    consistent: bool = True,
) -> str:
    config_line = (
        "quizbinf.env  the configuration, with every secret value replaced by a\n"
        "              placeholder. The keys tell you what has to be set on a new\n"
        "              host; the values are obtainable again from Canvas, the key\n"
        "              vault and yourself.\n"
        if had_config
        else "quizbinf.env  not present — this deployment is configured by environment\n"
        "              variables rather than by a file on the volume.\n"
    )
    return f"""quizbinf backup
taken {taken:%Y-%m-%d %H:%M} UTC

CONTAINS PERSONAL DATA. The database holds student names, their KTH usernames
and every answer they have given. Treat this file the way you would treat the
Participants view: keep it somewhere only you can read, and delete copies you
no longer need.

  quizbinf.db   the whole database. {db_bytes:,} bytes.
{_database_note(consistent)}
  images/       {image_count} uploaded figure(s). Questions reference these by
                path, so a database restored without them shows broken images.
  {config_line}
To restore, put quizbinf.db and images/ into the new deployment's data
directory (/home/data by default), fill in the secrets in quizbinf.env, and
start the app. It runs `alembic upgrade head` at startup, so a snapshot from an
older version migrates itself forward.

What this does NOT protect against: it is a copy you took by hand at one
moment. Anything answered after this file was made is not in it.

Check it before you rely on it:
  python3 -c "import sqlite3; print(sqlite3.connect('quizbinf.db')\
      .execute('PRAGMA integrity_check').fetchall())"
[('ok',)] means this is a restore point. Do it when you take the backup, not
when you need it.
"""


def _database_note(consistent: bool) -> str:
    if consistent:
        return (
            "                Taken with SQLite's VACUUM INTO, so it is a consistent copy\n"
            "                rather than a possibly torn file."
        )
    return (
        "\n"
        "                *** WARNING: THIS IS A RAW COPY, NOT A CLEAN SNAPSHOT ***\n"
        "                VACUUM INTO failed on the live database, which means it is\n"
        "                damaged. Rather than hand you nothing, this is a byte copy of\n"
        "                the file as it stood, plus its -wal (the recent commits live\n"
        "                there under WAL and are lost without it).\n"
        "                It may be torn and it may not open. To salvage what is in it:\n"
        "                    sqlite3 quizbinf.db .recover > rescued.sql\n"
        "                    sqlite3 rescued.db < rescued.sql\n"
        "                Then run PRAGMA integrity_check on rescued.db before using it."
    )
