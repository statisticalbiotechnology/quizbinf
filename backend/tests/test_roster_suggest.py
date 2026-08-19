"""The type-ahead on the login field, and the device binding behind it.

Both exist because roster login asks for no proof: the suggestion list makes
it usable, and the device binding stops the most obvious abuse of a login that
anyone can pass.
"""

import pytest

from app import service
from app.auth import DEVICE_COOKIE
from app.config import get_settings
from app.db import SessionLocal
from app.models import User
from app.throttle import suggest_throttle, teacher_login_throttle

TEACHER_PASSWORD = "correct horse battery staple"
COURSE = 63598


@pytest.fixture
def roster_enabled(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "roster_login", True)
    monkeypatch.setattr(settings, "roster_teacher_password", TEACHER_PASSWORD)
    monkeypatch.setattr(settings, "canvas_course_id", COURSE)
    monkeypatch.setattr(suggest_throttle, "_failures", {})
    monkeypatch.setattr(teacher_login_throttle, "_failures", {})
    return settings


@pytest.fixture
def class_of(teacher_client):
    """A roster with names that share prefixes, as a real class does."""
    db = SessionLocal()
    teacher = db.query(User).filter(User.username == "teach").one()
    students = [
        ("shiraza", "Shiraz Abbas"),
        ("shirin", "Shirin B"),
        ("ahmaa", "Ahmed Abdelmoez"),
        ("linaah2", "Lina Al-Hanbali"),
        ("sofiaali", "Sofia Ali"),
    ]
    service.sync_roster(
        db,
        teacher,
        COURSE,
        [
            {
                "canvas_user_id": 1000 + i,
                "kthid": f"u1{username}",
                "username": username,
                "display_name": name,
            }
            for i, (username, name) in enumerate(students)
        ],
    )
    db.close()


def test_typing_narrows_to_matching_addresses(client, roster_enabled, class_of):
    matches = client.get("/api/auth/roster-suggest?q=shir").json()["matches"]
    assert matches == ["shiraza@kth.se", "shirin@kth.se"]

    exact = client.get("/api/auth/roster-suggest?q=shiraza").json()["matches"]
    assert exact == ["shiraza@kth.se"]


def test_the_domain_can_be_typed_too(client, roster_enabled, class_of):
    """People type the whole address; matching is on the part before the @."""
    matches = client.get("/api/auth/roster-suggest?q=sofiaali@kth.se").json()["matches"]
    assert matches == ["sofiaali@kth.se"]


def test_nothing_is_offered_until_enough_has_been_typed(client, roster_enabled, class_of):
    """Otherwise one keystroke hands over a big slice of the class."""
    for short in ("", "s", "sh"):
        assert client.get(f"/api/auth/roster-suggest?q={short}").json()["matches"] == []


def test_matching_is_by_prefix_not_substring(client, roster_enabled, class_of):
    """A substring match would let 'ali' pull out unrelated classmates."""
    assert client.get("/api/auth/roster-suggest?q=ali").json()["matches"] == []
    assert client.get("/api/auth/roster-suggest?q=sof").json()["matches"] == [
        "sofiaali@kth.se"
    ]


def test_the_number_of_matches_is_capped(client, roster_enabled, teacher_client):
    db = SessionLocal()
    teacher = db.query(User).filter(User.username == "teach").one()
    service.sync_roster(
        db,
        teacher,
        COURSE,
        [
            {
                "canvas_user_id": 2000 + i,
                "kthid": f"u1x{i}",
                "username": f"anna{i:03d}",
                "display_name": f"Anna {i}",
            }
            for i in range(40)
        ],
    )
    db.close()

    matches = client.get("/api/auth/roster-suggest?q=anna").json()["matches"]
    assert len(matches) == service.SUGGEST_LIMIT


def test_suggestions_are_rate_limited(client, roster_enabled, class_of):
    """Enumeration by trying many prefixes should be slow."""
    for _ in range(60):
        client.get("/api/auth/roster-suggest?q=shir")

    assert client.get("/api/auth/roster-suggest?q=shir").json()["matches"] == []


def test_suggestions_only_cover_the_configured_course(client, roster_enabled, teacher_client):
    """A roster synced for another course must not leak into this one."""
    db = SessionLocal()
    teacher = db.query(User).filter(User.username == "teach").one()
    service.sync_roster(
        db,
        teacher,
        99999,
        [
            {
                "canvas_user_id": 5,
                "kthid": "u1old",
                "username": "oldstudent",
                "display_name": "Old Student",
            }
        ],
    )
    db.close()

    assert client.get("/api/auth/roster-suggest?q=oldstud").json()["matches"] == []


def test_suggestions_are_refused_when_roster_login_is_off(client, class_of):
    assert client.get("/api/auth/roster-suggest?q=shir").status_code == 403


# --- device binding --------------------------------------------------------


def test_a_device_is_bound_to_the_first_identity_it_claims(
    client, roster_enabled, class_of
):
    first = client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    assert first.status_code == 200
    assert DEVICE_COOKIE in client.cookies

    # The same phone cannot then answer as a classmate.
    second = client.post("/api/auth/roster-login", json={"email": "ahmaa@kth.se"})
    assert second.status_code == 409
    assert "already been used" in second.json()["detail"]


def test_signing_in_again_as_yourself_is_fine(client, roster_enabled, class_of):
    """The binding stops identity-switching, not ordinary re-login."""
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    client.post("/api/auth/logout")
    again = client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    assert again.status_code == 200


def test_logging_out_does_not_release_the_device(client, roster_enabled, class_of):
    """Otherwise the binding would be trivially bypassed by signing out."""
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    client.post("/api/auth/logout")
    assert (
        client.post("/api/auth/roster-login", json={"email": "ahmaa@kth.se"}).status_code == 409
    )


def test_another_device_is_unaffected(client, make_client, roster_enabled, class_of):
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})

    other_phone = make_client()
    assert (
        other_phone.post("/api/auth/roster-login", json={"email": "ahmaa@kth.se"}).status_code
        == 200
    )


def test_the_binding_expires(client, roster_enabled, class_of, monkeypatch):
    """A shared or replaced device recovers without anyone intervening."""
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    assert (
        client.post("/api/auth/roster-login", json={"email": "ahmaa@kth.se"}).status_code == 409
    )

    monkeypatch.setattr(get_settings(), "device_binding_hours", 0)
    assert (
        client.post("/api/auth/roster-login", json={"email": "ahmaa@kth.se"}).status_code == 200
    )


def test_teachers_are_not_device_bound(client, roster_enabled, class_of):
    """A teacher's laptop may well be used to demonstrate a student view."""
    client.post("/api/auth/roster-login", json={"email": "shiraza@kth.se"})
    resp = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": TEACHER_PASSWORD},
    )
    assert resp.status_code == 200
