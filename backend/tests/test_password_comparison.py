"""Comparing the shared teacher password.

Written after a password containing "ä" produced a 500 rather than a login:
`secrets.compare_digest` refuses `str` with non-ASCII characters, so the
comparison raised TypeError before it could compare anything.
"""

import unicodedata

import pytest

from app.auth import passwords_match
from app.config import get_settings
from app.throttle import teacher_login_throttle

SWEDISH_PASSWORD = "vårtermin-lösenord"


@pytest.fixture
def swedish_password(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "roster_login", True)
    monkeypatch.setattr(settings, "roster_teacher_password", SWEDISH_PASSWORD)
    monkeypatch.setattr(teacher_login_throttle, "_failures", {})
    return settings


def test_a_non_ascii_password_can_be_compared_at_all():
    """The regression: this raised TypeError rather than returning a bool."""
    assert passwords_match(SWEDISH_PASSWORD, SWEDISH_PASSWORD) is True
    assert passwords_match("something else", SWEDISH_PASSWORD) is False


def test_the_two_spellings_of_a_letter_match():
    """"ä" is one code point or two, depending on where it was typed. macOS
    tends to produce NFD, a file written on Linux holds NFC, and the bytes
    differ — so the same password typed and stored would not match."""
    composed = unicodedata.normalize("NFC", "lösenord")
    decomposed = unicodedata.normalize("NFD", "lösenord")
    assert composed != decomposed  # guard against a vacuous test

    assert passwords_match(decomposed, composed) is True
    assert passwords_match(composed, decomposed) is True


def test_an_empty_or_missing_password_is_simply_wrong():
    assert passwords_match(None, SWEDISH_PASSWORD) is False
    assert passwords_match("", SWEDISH_PASSWORD) is False
    # And an unconfigured password is not satisfied by an empty attempt.
    assert passwords_match("", "") is True  # only reachable when disabled
    assert passwords_match("guess", None) is False


def test_ascii_passwords_still_work():
    assert passwords_match("plain-ascii", "plain-ascii") is True
    assert passwords_match("plain-ascii", "plain-asciX") is False


def test_a_teacher_signs_in_with_a_swedish_password(client, swedish_password):
    resp = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": SWEDISH_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "teacher"


def test_a_wrong_swedish_password_is_401_not_500(client, swedish_password):
    resp = client.post(
        "/api/auth/roster-login",
        json={"email": "teach@kth.se", "password": "vårtermin-fel"},
    )
    assert resp.status_code == 401


def test_the_password_typed_in_the_other_normalisation_works(client, swedish_password):
    """A teacher typing on a Mac must reach a password stored on Linux."""
    resp = client.post(
        "/api/auth/roster-login",
        json={
            "email": "teach@kth.se",
            "password": unicodedata.normalize("NFD", SWEDISH_PASSWORD),
        },
    )
    assert resp.status_code == 200, resp.text
