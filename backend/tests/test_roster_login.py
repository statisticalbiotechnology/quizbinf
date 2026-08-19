"""Identifying students against the synced roster.

A stop-gap for running the course before a real identity provider exists.
It is identification, not authentication — the tests below say so explicitly
where that is the behaviour being pinned, so nobody later mistakes a gap for
a bug and "fixes" it by loosening something that matters.
"""

import pytest

from app import service
from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.throttle import Throttle, teacher_login_throttle

TEACHER_PASSWORD = "correct horse battery staple"


@pytest.fixture
def roster_enabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "roster_login", True)
    monkeypatch.setattr(settings, "roster_teacher_password", TEACHER_PASSWORD)
    # A fresh throttle per test, so lockouts do not leak between them.
    monkeypatch.setattr(teacher_login_throttle, "_failures", {})
    return settings


@pytest.fixture
def enrolled(teacher_client):
    """One student on the roster of a synced course."""
    db = SessionLocal()
    teacher = db.query(User).filter(User.username == "teach").one()
    service.sync_roster(
        db,
        teacher,
        49207,
        [
            {
                "canvas_user_id": 109957,
                "kthid": "u17z7fwu",
                "username": "shiraza",
                "display_name": "Shiraz Abbas",
            }
        ],
    )
    db.close()


def test_a_student_on_the_roster_is_let_in(client, roster_enabled, enrolled):
    resp = client.post(
        "/api/auth/roster-login", json={"email": "shiraza@kth.se", "password": ""}
    )
    assert resp.status_code == 200
    user = resp.json()
    assert user["username"] == "shiraza"
    assert user["role"] == "student"
    # And the session works afterwards.
    assert client.get("/api/auth/me").json()["username"] == "shiraza"


def test_the_display_name_comes_from_the_roster(client, roster_enabled, enrolled):
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    assert client.get("/api/auth/me").json()["display_name"] == "Shiraz Abbas"


@pytest.mark.parametrize("email", ["SHIRAZA@KTH.SE", " shiraza@kth.se ", "shiraza"])
def test_the_address_is_normalised_the_same_way_the_sync_does(
    client, roster_enabled, enrolled, email
):
    """Login and sync must agree on normalisation, or nothing ever matches."""
    assert client.post("/api/auth/roster-login", json={"email": email}).status_code == 200


def test_someone_not_on_the_roster_is_refused(client, roster_enabled, enrolled):
    resp = client.post("/api/auth/roster-login", json={"email": "stranger@kth.se"})
    assert resp.status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_the_refusal_does_not_reveal_who_is_enrolled(client, roster_enabled, enrolled):
    """The endpoint must not become a way to test course membership."""
    empty_roster = client.post("/api/auth/roster-login", json={"email": "nobody@kth.se"})
    assert empty_roster.status_code == 401
    assert "not on the course roster" in empty_roster.json()["detail"]
    # No name, no course, nothing about who *is* on it.
    assert "shiraza" not in empty_roster.text.lower()


def test_a_student_cannot_become_a_teacher_by_guessing(client, roster_enabled, enrolled):
    """The roster grants the student role only; teachers come from the
    configured allowlist and need the password."""
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    assert client.get("/api/auth/me").json()["role"] == "student"
    # Teacher-only endpoints stay closed.
    assert client.get("/api/quizzes").status_code == 403


def test_a_teacher_needs_the_shared_password(client, roster_enabled):
    blank = client.post("/api/auth/roster-login", json={"email": "teach@kth.se", "password": ""})
    assert blank.status_code == 401

    wrong = client.post(
        "/api/auth/roster-login", json={"email": "teach@kth.se", "password": "guess"}
    )
    assert wrong.status_code == 401

    right = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": TEACHER_PASSWORD},
    )
    assert right.status_code == 200
    assert right.json()["role"] == "teacher"


def test_a_teacher_does_not_need_to_be_on_the_roster(client, roster_enabled):
    """Teachers are enrolled as teachers in Canvas, not as students, so they
    never appear in a student roster."""
    resp = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": TEACHER_PASSWORD},
    )
    assert resp.status_code == 200


def test_guessing_the_teacher_password_gets_locked_out(client, roster_enabled):
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/roster-login", json={"email": "teach@kth.se", "password": "no"}
            ).status_code
            == 401
        )

    locked = client.post(
        "/api/auth/roster-login", json={"email": "teach@kth.se", "password": "no"}
    )
    assert locked.status_code == 429
    # Even the right password waits: otherwise the lockout is no lockout.
    still_locked = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": TEACHER_PASSWORD},
    )
    assert still_locked.status_code == 429


def test_a_successful_login_clears_the_failure_count(client, roster_enabled):
    for _ in range(3):
        client.post("/api/auth/roster-login", json={"email": "teach@kth.se", "password": "no"})
    assert (
        client.post(
            "/api/auth/roster-login",
            json={"email": "teach@kth.se", "password": TEACHER_PASSWORD},
        ).status_code
        == 200
    )
    # The next mistake starts from zero rather than tipping straight over.
    assert (
        client.post(
            "/api/auth/roster-login", json={"email": "teach@kth.se", "password": "no"}
        ).status_code
        == 401
    )


def test_roster_login_is_refused_when_switched_off(client, enrolled):
    assert (
        client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"}).status_code == 403
    )


def test_it_is_refused_without_a_teacher_password(client, monkeypatch, enrolled):
    """Enabled but with no teacher password is a misconfiguration, not a mode:
    a blank password would let any student sign in as a teacher."""
    settings = get_settings()
    monkeypatch.setattr(settings, "roster_login", True)
    monkeypatch.setattr(settings, "roster_teacher_password", "")

    assert settings.roster_login_allowed is False
    assert (
        client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"}).status_code == 403
    )


def test_the_login_page_is_told_which_methods_exist(client, roster_enabled):
    methods = client.get("/api/auth/methods").json()
    assert methods["roster_login"] is True
    assert methods["oidc"] is False
    # The shared password is never described to a client.
    assert TEACHER_PASSWORD not in client.get("/api/auth/methods").text


def test_the_throttle_window_expires():
    throttle = Throttle(max_failures=2, lockout=60)
    throttle.record_failure("a")
    throttle.record_failure("a")
    assert throttle.locked_for("a") > 0

    # Pretend the lockout has elapsed.
    count, started = throttle._failures["a"]
    throttle._failures["a"] = (count, started - 61)
    assert throttle.locked_for("a") == 0


def test_one_clients_lockout_does_not_block_another():
    throttle = Throttle(max_failures=1, lockout=60)
    throttle.record_failure("192.0.2.1")
    assert throttle.locked_for("192.0.2.1") > 0
    assert throttle.locked_for("192.0.2.2") == 0
