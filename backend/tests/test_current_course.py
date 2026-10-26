"""Which Canvas course the attendance files use.

It used to be `CANVAS_COURSE_ID`, defaulting to one fixed course, so a new
term kept matching students against last year's roster unless somebody edited
the configuration file. Unset, it is now the course synced most recently:
syncing the new course is something the teacher does anyway.
"""

import time

from app import service
from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from tests.conftest import login
from tests.test_export import _rows, _run_one_question_session


def _sync(course_id: int, canvas_user_id: int, username: str) -> None:
    with SessionLocal() as db:
        teacher = db.query(User).filter(User.username == "teach").one()
        service.sync_roster(
            db,
            teacher,
            course_id,
            [{"canvas_user_id": canvas_user_id, "kthid": f"u1{username}",
              "username": username, "display_name": username.title()}],
        )
    time.sleep(0.01)  # distinct sync times, so "most recent" is unambiguous


def _canvas_ids(teacher_client) -> dict[str, str]:
    """SIS Login ID -> Canvas ID, from the term file with no course given."""
    rows = _rows(teacher_client.get("/api/reports/canvas-participation.csv").text)
    return {r[3]: r[1] for r in rows[2:]}


def test_with_nothing_configured_the_last_synced_course_is_used(teacher_client, make_client):
    student = make_client()
    login(student, "nyelev")
    _run_one_question_session(teacher_client, [student])

    # Last year's course, then this year's: the student is only in the new one.
    _sync(63598, 11, "someoneelse")
    _sync(70001, 22, "nyelev")

    status = teacher_client.get("/api/roster/status").json()
    assert status["current_course_id"] == 70001
    assert status["course_pinned"] is False
    assert _canvas_ids(teacher_client) == {"nyelev": "22"}

    # Re-syncing the old course makes it current again: the last sync decides.
    _sync(63598, 11, "someoneelse")
    assert teacher_client.get("/api/roster/status").json()["current_course_id"] == 63598


def test_a_configured_course_still_wins(teacher_client, make_client, monkeypatch):
    student = make_client()
    login(student, "nyelev")
    _run_one_question_session(teacher_client, [student])
    _sync(70001, 22, "nyelev")
    _sync(63598, 11, "someoneelse")

    monkeypatch.setattr(get_settings(), "canvas_course_id", 70001)
    status = teacher_client.get("/api/roster/status").json()
    assert status["current_course_id"] == 70001
    assert status["course_pinned"] is True
    assert _canvas_ids(teacher_client) == {"nyelev": "22"}


def test_before_any_sync_the_files_still_download(teacher_client, make_client):
    """No course yet: the file is produced with blank ids rather than failing,
    and the readiness check says nothing is synced."""
    student = make_client()
    login(student, "nyelev")
    _run_one_question_session(teacher_client, [student])

    assert teacher_client.get("/api/roster/status").json()["current_course_id"] is None
    assert _canvas_ids(teacher_client) == {"nyelev": ""}
