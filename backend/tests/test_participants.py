"""Room size for the projected join screen.

Answers cannot tell the teacher how many people are present before a round
opens, so opening the session is recorded separately.
"""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import login, make_quiz_with_question


def _session(teacher_client) -> str:
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    return teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]


def test_nobody_has_joined_a_fresh_session(teacher_client):
    code = _session(teacher_client)
    assert teacher_client.get(f"/api/sessions/{code}/participants").json()["joined"] == 0


def test_opening_the_session_counts_as_joining(student_client, teacher_client):
    code = _session(teacher_client)
    student_client.get(f"/api/sessions/{code}/state")
    assert teacher_client.get(f"/api/sessions/{code}/participants").json()["joined"] == 1


def test_each_student_counted_once_however_often_they_reload(student_client, teacher_client):
    code = _session(teacher_client)
    for _ in range(4):
        student_client.get(f"/api/sessions/{code}/state")
    assert teacher_client.get(f"/api/sessions/{code}/participants").json()["joined"] == 1


def test_distinct_students_add_up(teacher_client):
    code = _session(teacher_client)
    for name in ("anna", "bo", "cecilia"):
        c = TestClient(app)
        login(c, name)
        c.get(f"/api/sessions/{code}/state")
    assert teacher_client.get(f"/api/sessions/{code}/participants").json()["joined"] == 3


def test_participants_are_counted_not_named(student_client, teacher_client):
    """Projecting the join screen must never expose who is in the room."""
    code = _session(teacher_client)
    student_client.get(f"/api/sessions/{code}/state")
    body = teacher_client.get(f"/api/sessions/{code}/participants").json()
    assert set(body) == {"joined", "connected"}
    assert "student1" not in str(body)


def test_the_teacher_running_the_session_is_not_counted(teacher_client):
    """The teacher's own views poll /state; they are not in the room."""
    code = _session(teacher_client)
    for _ in range(3):
        teacher_client.get(f"/api/sessions/{code}/state")
    assert teacher_client.get(f"/api/sessions/{code}/participants").json()["joined"] == 0


def test_participants_are_teacher_only(student_client, teacher_client):
    code = _session(teacher_client)
    assert student_client.get(f"/api/sessions/{code}/participants").status_code == 403
