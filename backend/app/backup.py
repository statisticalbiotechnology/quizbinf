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

import re
import secrets
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

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


def snapshot_database(settings: Settings, destination: Path) -> None:
    """Copy the database consistently, without blocking the lecture.

    `VACUUM INTO` reads through a normal transaction, so a session running at
    the same time carries on. Copying the file byte-for-byte instead can catch
    it mid-write and produce a snapshot that will not open.
    """
    source = sqlite_path(settings)
    connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        connection.execute("VACUUM INTO ?", (str(destination),))
    finally:
        connection.close()


def build(settings: Settings, workspace: Path) -> Path:
    """Assemble the archive under `workspace` and return its path."""
    taken = datetime.now(timezone.utc)
    archive = workspace / f"quizbinf-backup-{taken:%Y-%m-%d}.zip"

    # A unique name, not "quizbinf.db": `VACUUM INTO` refuses to overwrite,
    # and naming the snapshot after the source makes the two collide the
    # moment the workspace and the data directory are the same place.
    database = workspace / f"snapshot-{secrets.token_hex(8)}.db"
    snapshot_database(settings, database)

    data = Path(settings.data_dir)
    images = sorted(p for p in (data / "images").glob("*") if p.is_file())
    config = data / "quizbinf.env"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(database, "quizbinf.db")
        for image in images:
            bundle.write(image, f"images/{image.name}")
        if config.is_file():
            bundle.writestr(
                "quizbinf.env", redact_config(config.read_text(encoding="utf-8"))
            )
        bundle.writestr(
            "README.txt",
            _readme(taken, database.stat().st_size, len(images), config.is_file()),
        )

    database.unlink()
    return archive


def _readme(taken: datetime, db_bytes: int, image_count: int, had_config: bool) -> str:
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

  quizbinf.db   the whole database, taken with SQLite's VACUUM INTO so it is a
                consistent copy rather than a possibly torn file. {db_bytes:,} bytes.
  images/       {image_count} uploaded figure(s). Questions reference these by
                path, so a database restored without them shows broken images.
  {config_line}
To restore, put quizbinf.db and images/ into the new deployment's data
directory (/home/data by default), fill in the secrets in quizbinf.env, and
start the app. It runs `alembic upgrade head` at startup, so a snapshot from an
older version migrates itself forward.

What this does NOT protect against: it is a copy you took by hand at one
moment. Anything answered after this file was made is not in it.
"""
