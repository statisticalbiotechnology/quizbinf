"""Rehearsing a lecture against a deployment that has real students in it.

The app most worth load-testing is the deployed one — the small machine, the
real proxy, the real volume — and that one has no scriptable login, because
its only login is KTH's IdP. `LOADTEST_KEY` opens a narrow door for it.

A door into a database holding a real class is worth being exact about, so
each property that keeps it narrow is pinned here rather than left to the
docstring: it cannot be opened without a key, it cannot mint a teacher, it
cannot produce a username that collides with a real one, what it writes is
invisible to every attendance report, and what it leaves behind can be
removed.
"""

import pytest

from app.config import Settings, get_settings
from app.routers.auth import LOADTEST_PREFIX
from app.service import LOADTEST_PREFIX as SERVICE_PREFIX
from tests.conftest import login, make_quiz_with_question

KEY = "a-long-enough-loadtest-key-0123456789"


@pytest.fixture
def loadtest_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "loadtest_key", KEY)
    return KEY


def _sign_in(client, name: str, key: str = KEY):
    return client.post("/api/auth/loadtest-login", json={"key": key, "name": name})


# --- the door ---------------------------------------------------------------


def test_it_is_shut_unless_a_key_is_configured(client):
    """The default. No key, no endpoint — this is the state every deployment
    is in until somebody deliberately changes it."""
    assert not get_settings().loadtest_allowed
    assert _sign_in(client, "anna").status_code == 403


def test_a_short_key_does_not_count_as_configured(monkeypatch, client):
    """A guessable key on a door that creates accounts is worse than no door.
    Refused as unconfigured rather than accepted as weak."""
    monkeypatch.setattr(get_settings(), "loadtest_key", "hunter2")
    assert not get_settings().loadtest_allowed
    assert _sign_in(client, "anna", key="hunter2").status_code == 403


def test_the_wrong_key_is_refused(loadtest_on, client):
    assert _sign_in(client, "anna", key="x" * 40).status_code == 401


def test_it_signs_in_a_throwaway_student(loadtest_on, client):
    response = _sign_in(client, "s001")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == f"{LOADTEST_PREFIX}s001"
    assert body["role"] == "student"
    # And the cookie works, or the whole thing is decorative.
    assert client.get("/api/auth/me").json()["username"] == f"{LOADTEST_PREFIX}s001"


def test_it_cannot_mint_a_teacher(loadtest_on, client):
    """The property the key most needs.

    Teacher views hold every student's participation record, so a key that
    could produce a teacher would be a way to read a whole class's personal
    data. Asking for the configured teacher's own name must not do it — nor
    may the prefix be escaped to reach that name in the first place.
    """
    teacher_name = next(iter(get_settings().teachers))

    body = _sign_in(client, teacher_name).json()

    assert body["role"] == "student"
    assert body["username"] == LOADTEST_PREFIX + teacher_name
    assert client.get("/api/backup.zip").status_code == 403
    assert client.get("/api/reports/participation.csv").status_code == 403


@pytest.mark.parametrize("name", ["../teach", "teach ", "TEACH", "a.b", "x" * 41, ""])
def test_a_name_that_could_escape_the_prefix_is_refused(loadtest_on, client, name):
    """The prefix is the whole reason a rehearsal cannot impersonate anyone,
    so what may go after it is restricted at the schema."""
    assert _sign_in(client, name).status_code == 422


def test_the_two_prefixes_agree():
    """`service` keeps its own copy to avoid importing a router; if the two
    ever diverged, the purge would silently stop finding what to purge."""
    assert LOADTEST_PREFIX == SERVICE_PREFIX


# --- what a rehearsal does to the reports -----------------------------------


def _run_a_bout(teacher_client, student, quiz_id, question_id, choice_id, loadtest: bool):
    code = teacher_client.post(
        f"/api/sessions?quiz_id={quiz_id}&loadtest={'true' if loadtest else 'false'}"
    ).json()["code"]
    for phase in ("pre", "post"):
        round_ = teacher_client.post(
            f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": phase}
        ).json()
        student.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_id})
        teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")
    return code


def test_a_rehearsal_is_not_a_lecture_in_the_attendance_reports(
    teacher_client, make_client, loadtest_on
):
    """The reason the flag exists at all.

    Both attendance reports walk every session their owner has run, and a
    session that ran rounds is a lecture to them. A rehearsal left in the
    denominator would mark the whole real class absent from a lecture that
    never happened — and nothing deletes a session, so it would stand.
    """
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    real_student = make_client()
    login(real_student, "anna")
    fake = make_client()
    _sign_in(fake, "s001")

    _run_a_bout(teacher_client, real_student, quiz_id, question_id, choice_ids[0], loadtest=False)
    _run_a_bout(teacher_client, fake, quiz_id, question_id, choice_ids[0], loadtest=True)

    csv = teacher_client.get("/api/reports/canvas-participation.csv").text
    assert "loadtest-" not in csv, "a throwaway student reached the gradebook file"

    report = teacher_client.get("/api/reports/participation").json()
    assert len(report["sessions"]) == 1, "the rehearsal counted as a lecture"
    # Anna answered the one real lecture in full, so she must still score it.
    rows = {r["username"]: r for r in report["students"]}
    assert "anna" in rows
    assert "loadtest-s001" not in rows


# --- undoing one -----------------------------------------------------------


def test_a_rehearsal_can_be_deleted_along_with_the_students_it_invented(
    teacher_client, make_client, loadtest_on
):
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    fake = make_client()
    _sign_in(fake, "s001")
    code = _run_a_bout(teacher_client, fake, quiz_id, question_id, choice_ids[0], loadtest=True)

    removed = teacher_client.delete(f"/api/sessions/{code}").json()

    assert removed["rounds"] == 2
    assert removed["answers"] == 2
    assert removed["users"] == 1
    assert teacher_client.get(f"/api/sessions/{code}/state").status_code == 404
    # The throwaway account is gone, not merely orphaned.
    assert _sign_in(make_client(), "s001").json()["username"] == "loadtest-s001"


def test_a_real_session_cannot_be_deleted(teacher_client, make_client):
    """Answers are the one irreplaceable thing here, and a six-character code
    is easy to mistype. The endpoint refuses rather than asks."""
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    student = make_client()
    login(student, "anna")
    code = _run_a_bout(teacher_client, student, quiz_id, question_id, choice_ids[0], loadtest=False)

    response = teacher_client.delete(f"/api/sessions/{code}")

    assert response.status_code == 409
    assert teacher_client.get(f"/api/sessions/{code}/state").status_code == 200
    histogram = teacher_client.get(f"/api/sessions/{code}/participation").json()
    assert histogram["rows"], "a refused delete must leave the answers alone"


def test_deleting_a_rehearsal_is_teacher_only(teacher_client, student_client, loadtest_on):
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}&loadtest=true").json()["code"]
    assert student_client.delete(f"/api/sessions/{code}").status_code == 403


def test_a_real_session_is_the_default(teacher_client):
    """`loadtest` has to be asked for. A flag that defaulted the other way
    would quietly drop a real lecture out of the attendance record."""
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    assert teacher_client.delete(f"/api/sessions/{code}").status_code == 409


def test_a_settings_object_keeps_the_key_out_of_sight():
    """It is a credential, so it must be redacted from the backup archive the
    same way every other one is."""
    from app import backup

    out = backup.redact_config(f"LOADTEST_KEY={KEY}\n")
    assert KEY not in out
    assert "LOADTEST_KEY" in out


def test_the_key_is_not_advertised(client, loadtest_on):
    """`/api/auth/methods` drives the login page. A deployment being
    rehearsable is not something a visitor needs to know."""
    assert "loadtest" not in client.get("/api/auth/methods").text.lower()
    assert Settings(loadtest_key=KEY).loadtest_allowed
