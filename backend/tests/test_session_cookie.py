"""Why a cookie was rejected.

An expired cookie and a cookie signed with the wrong secret both mean "log in
again" to the user, but they mean very different things to whoever is
debugging a deployment: one is routine, the other says the server's session
secret is not what it was. SignatureExpired subclasses BadSignature, so
catching only the latter reports every routine expiry as a forged cookie.
"""

import pytest
from itsdangerous import URLSafeTimedSerializer

from app.auth import COOKIE_NAME, SESSION_MAX_AGE
from app.config import get_settings
from tests.conftest import login


@pytest.fixture
def logged_in(client):
    login(client, "teach")
    return client


def test_a_valid_cookie_is_accepted(logged_in):
    assert logged_in.get("/api/auth/me").status_code == 200


def test_an_expired_cookie_says_so(logged_in, monkeypatch):
    # Age the cookie past the window rather than waiting 12 hours for it.
    monkeypatch.setattr("app.auth.SESSION_MAX_AGE", -1)

    resp = logged_in.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Session expired"


def test_a_cookie_signed_with_another_secret_is_invalid(client):
    # What a changed session secret actually looks like: a well-formed, in-date
    # cookie that this server cannot verify.
    forged = URLSafeTimedSerializer("not-the-real-secret", salt="quizbinf-auth")
    client.cookies.set(COOKIE_NAME, forged.dumps({"username": "teach"}))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid session"


def test_no_cookie_is_reported_as_not_logged_in(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Not logged in"


def test_the_session_secret_is_stable_across_calls():
    # A per-process secret would invalidate every cookie on restart; this is
    # the property that makes "Invalid session" meaningful as a signal.
    assert get_settings().resolved_session_secret == get_settings().resolved_session_secret
    assert SESSION_MAX_AGE > 0
