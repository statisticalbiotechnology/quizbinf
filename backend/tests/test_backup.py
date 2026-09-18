"""The backup archive.

Two things matter here and they pull against each other. The archive has to be
complete enough to bring a deployment back — which is checked by restoring
from it and reading the answers out — and it must not become a credential,
which is checked by looking for every secret the app holds.
"""

import io
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app import backup
from tests.conftest import login, make_quiz_with_question


def _archive(client) -> zipfile.ZipFile:
    response = client.get("/api/backup.zip")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    return zipfile.ZipFile(io.BytesIO(response.content))


def _lecture_with_an_answer(teacher_client, make_client) -> tuple[str, str]:
    """Run one bout so there is an answer worth not losing."""
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    student = make_client()
    login(student, "anna")
    round_ = teacher_client.post(
        f"/api/sessions/{code}/rounds",
        json={"question_id": question_id, "phase": "pre"},
    ).json()
    student.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")
    return code, "anna"


# --- completeness ----------------------------------------------------------


def test_the_archive_restores_to_a_working_database(teacher_client, make_client, tmp_path):
    """The point of the whole feature: the answers come back.

    Restoring means opening the snapshot as a database and finding the row,
    not merely finding a file of plausible size in the zip.
    """
    _lecture_with_an_answer(teacher_client, make_client)

    with _archive(teacher_client) as bundle:
        restored = tmp_path / "restored.db"
        restored.write_bytes(bundle.read("quizbinf.db"))

    connection = sqlite3.connect(restored)
    try:
        answers = connection.execute(
            "SELECT count(*) FROM answers JOIN users ON users.id = answers.user_id "
            "WHERE users.username = 'anna'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert answers == 1, "the restored database has lost the student's answer"


def test_the_snapshot_is_consistent_rather_than_a_file_copy(teacher_client, tmp_path):
    """`VACUUM INTO` writes a whole database, so the copy opens cleanly even if
    the app was mid-transaction when it was taken. A byte-for-byte copy of a
    live SQLite file can be torn, and `PRAGMA integrity_check` is what tells
    the difference."""
    with _archive(teacher_client) as bundle:
        snapshot = tmp_path / "snapshot.db"
        snapshot.write_bytes(bundle.read("quizbinf.db"))

    connection = sqlite3.connect(snapshot)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_uploaded_figures_travel_with_the_database(teacher_client, tmp_path, monkeypatch):
    """A question references a figure by path; without the files a restored
    database renders broken images."""
    from app.config import get_settings

    settings = get_settings()
    images = Path(settings.data_dir) / "images"
    images.mkdir(parents=True, exist_ok=True)
    (images / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\nnot-really-a-png")

    try:
        with _archive(teacher_client) as bundle:
            assert "images/figure.png" in bundle.namelist()
            assert bundle.read("images/figure.png").startswith(b"\x89PNG")
    finally:
        (images / "figure.png").unlink()


def test_the_archive_says_what_it_is_and_that_it_holds_personal_data(teacher_client):
    with _archive(teacher_client) as bundle:
        readme = bundle.read("README.txt").decode()
    assert "PERSONAL DATA" in readme
    assert "restore" in readme.lower()


# --- what must never be in it ----------------------------------------------


def test_secrets_are_redacted_from_the_configuration(teacher_client, tmp_path):
    """The archive lands in a downloads folder. The Canvas token acts as the
    teacher *in Canvas*, where the grades are, and the app's own invariant is
    that it never reaches a client — so it must not ride out inside a zip."""
    from app.config import get_settings

    settings = get_settings()
    config = Path(settings.data_dir) / "quizbinf.env"
    config.write_text(
        "CANVAS_TOKEN=7~realtokenvalue\n"
        "OIDC_CLIENT_SECRET=realclientsecret\n"
        "ROSTER_TEACHER_PASSWORD=realpassword\n"
        "SESSION_SECRET=realsessionsecret\n"
        "TEACHER_USERNAMES=lukask\n"
        "CANVAS_COURSE_ID=63598\n",
        encoding="utf-8",
    )

    try:
        response = teacher_client.get("/api/backup.zip")
        whole_archive = response.content
        with zipfile.ZipFile(io.BytesIO(whole_archive)) as bundle:
            env = bundle.read("quizbinf.env").decode()
    finally:
        config.unlink()

    for secret in (
        "realtokenvalue",
        "realclientsecret",
        "realpassword",
        "realsessionsecret",
    ):
        assert secret not in env
        # Not just absent from that file — absent from the archive entirely.
        assert secret.encode() not in whole_archive

    # The keys survive: they are what tells you what to set on a new host.
    for key in ("CANVAS_TOKEN", "OIDC_CLIENT_SECRET", "ROSTER_TEACHER_PASSWORD"):
        assert key in env
    assert "TEACHER_USERNAMES=lukask" in env, "non-secrets must be kept"


def test_a_password_inside_a_database_url_is_redacted_too():
    """It is not named like a secret, but it is one."""
    out = backup.redact_config(
        "DATABASE_URL=postgresql+psycopg://quizbinf:hunter2@db:5432/quizbinf\n"
    )
    assert "hunter2" not in out
    assert "postgresql+psycopg://quizbinf:" in out
    assert "@db:5432/quizbinf" in out


def test_an_unrecognised_secret_is_redacted_by_default():
    """Matched by pattern, not by a list, so a setting added later is covered
    without anyone remembering to update this."""
    out = backup.redact_config("SOME_FUTURE_API_TOKEN=abc123\nPLAIN_SETTING=visible\n")
    assert "abc123" not in out
    assert "PLAIN_SETTING=visible" in out


def test_comments_and_blank_lines_survive_redaction():
    text = "# how this is configured\n\nTEACHER_USERNAMES=lukask\n"
    assert backup.redact_config(text) == text


def test_the_snapshot_cannot_collide_with_the_database_it_copies(tmp_path):
    """`VACUUM INTO` refuses to overwrite, so naming the snapshot after its
    source breaks as soon as the workspace and the data directory coincide —
    which is exactly what a caller passing the data directory would do."""
    from app.config import Settings

    sqlite3.connect(tmp_path / "quizbinf.db").executescript(
        "CREATE TABLE t(id INTEGER PRIMARY KEY); INSERT INTO t VALUES (1);"
    )
    settings = Settings(
        data_dir=str(tmp_path), database_url=f"sqlite:///{tmp_path}/quizbinf.db"
    )

    archive = backup.build(settings, tmp_path)

    with zipfile.ZipFile(archive) as bundle:
        assert "quizbinf.db" in bundle.namelist()


# --- who may take one ------------------------------------------------------


def test_the_backup_is_teacher_only(student_client, make_client):
    """It is the whole database: every student's name and every answer."""
    assert student_client.get("/api/backup.zip").status_code == 403
    assert make_client().get("/api/backup.zip").status_code == 401


# --- deployments the app cannot snapshot -----------------------------------


def test_a_non_sqlite_deployment_has_no_sqlite_path(monkeypatch):
    """Nothing may guess a filename out of a URL that has none."""
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "database_url", "postgresql+psycopg://u:p@db:5432/quizbinf"
    )
    try:
        backup.sqlite_path(settings)
    except backup.NotSupported as e:
        assert "postgresql+psycopg" in str(e)
    else:
        raise AssertionError("a Postgres deployment has no SQLite file to point at")


def test_a_backup_never_describes_itself_as_more_than_it_is():
    """The concern the old refusal was protecting, kept without the refusal.

    This endpoint used to decline on anything but SQLite and tell the teacher
    to run `pg_dump` — advice, not a backup: they have a browser and a session
    cookie, not a shell on the database host. Moving to PostgreSQL would have
    quietly removed the one button that rescues this app's data.

    It now renders any database into a portable SQLite file. What must not
    happen is the original worry: an archive that *looks* like a whole-server
    dump. The three kinds of copy have to be distinguishable inside the
    archive, because they are read months later by somebody in a hurry.
    """
    vacuumed = backup._readme(
        datetime.now(timezone.utc), 1024, 0, False, backup.VACUUMED
    )
    raw = backup._readme(datetime.now(timezone.utc), 1024, 0, False, backup.RAW)
    copied = backup._readme(datetime.now(timezone.utc), 1024, 0, False, backup.COPIED)

    assert "VACUUM INTO" in vacuumed
    assert "RAW COPY" not in vacuumed

    assert "RAW COPY" in raw
    assert ".recover" in raw

    assert "not SQLite" in copied
    assert "NOT a substitute" in copied, "it must not read as a whole-server dump"
    assert "pg_dump" in copied, "and it must name the tool that is one"


# --- the certificate the database connection needs -------------------------


def test_the_ca_certificate_named_by_the_database_url_is_found(tmp_path):
    """`sslmode=verify-full` is fail-closed, which makes this file a startup
    dependency: without it libpq refuses the connection and the app cannot
    open the database at all. It lived only on the volume and was in no
    backup, so restoring onto a fresh volume produced a deployment that would
    not start."""
    from app.config import Settings

    ca = tmp_path / "quizbinf-pg-ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nnot really one\n")

    settings = Settings(
        data_dir=str(tmp_path),
        database_url=(
            "postgresql+psycopg://u:p@heisenberg.scilifelab.se:5432/quizbinf"
            f"?sslmode=verify-full&sslrootcert={ca}"
        ),
    )

    assert backup.tls_files(settings) == [ca]


def test_a_private_key_never_travels(tmp_path):
    """Only the public half. A CA certificate is published so that clients can
    verify against it; a client key is a credential, and the rule that keeps
    the Canvas token out of this archive keeps a key out of it too."""
    from app.config import Settings

    ca = tmp_path / "ca.crt"
    ca.write_text("ca")
    cert = tmp_path / "client.crt"
    cert.write_text("cert")
    key = tmp_path / "client.key"
    key.write_text("PRIVATE KEY")

    settings = Settings(
        data_dir=str(tmp_path),
        database_url=(
            "postgresql+psycopg://u:p@db:5432/quizbinf?sslmode=verify-full"
            f"&sslrootcert={ca}&sslcert={cert}&sslkey={key}"
        ),
    )

    assert backup.tls_files(settings) == [ca]


def test_a_sqlite_deployment_carries_no_certificate(tmp_path):
    """Nothing crosses a network, so there is nothing to verify."""
    from app.config import Settings

    settings = Settings(data_dir=str(tmp_path))
    assert backup.tls_files(settings) == []


def test_the_certificate_is_in_the_archive_and_the_readme_says_where_it_goes(
    teacher_client, tmp_path, monkeypatch
):
    """The path matters as much as the file: DATABASE_URL names it absolutely,
    so a restore that puts it somewhere else still cannot connect."""
    ca = tmp_path / "quizbinf-pg-ca.crt"
    ca.write_text("-----BEGIN CERTIFICATE-----\nthe-ca-bytes\n")
    monkeypatch.setattr(backup, "tls_files", lambda settings: [ca])

    with _archive(teacher_client) as bundle:
        assert "quizbinf-pg-ca.crt" in bundle.namelist()
        assert b"the-ca-bytes" in bundle.read("quizbinf-pg-ca.crt")
        readme = bundle.read("README.txt").decode()

    assert str(ca) in readme, "the README must name the path it has to go back to"
    assert "not a secret" in readme


def test_the_readme_stays_quiet_when_there_is_no_certificate():
    """No stray prose about certificates on a deployment that has none."""
    readme = backup._readme(datetime.now(timezone.utc), 1024, 0, False, backup.VACUUMED)
    assert "certificate" not in readme.lower()
