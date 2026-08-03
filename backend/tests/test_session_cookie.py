"""Why a cookie was rejected.

An expired cookie and a cookie signed with the wrong secret both mean "log in
again" to the user, but they mean very different things to whoever is
debugging a deployment: one is routine, the other says the server's session
secret is not what it was. SignatureExpired subclasses BadSignature, so
catching only the latter reports every routine expiry as a forged cookie.
"""

import time

import pytest
from itsdangerous import URLSafeTimedSerializer

from app.auth import COOKIE_NAME, SESSION_MAX_AGE, SESSION_RENEW_AFTER
from app.config import get_settings
from tests.conftest import login, make_quiz_with_question


@pytest.fixture
def logged_in(client):
    login(client, "teach")
    return client


def _issued_at(token: str):
    """When a cookie was signed, read back out of the token itself."""
    serializer = URLSafeTimedSerializer(
        get_settings().resolved_session_secret, salt="quizbinf-auth"
    )
    return serializer.loads(token, return_timestamp=True)[1]


def test_a_valid_cookie_is_accepted(logged_in):
    assert logged_in.get("/api/auth/me").status_code == 200


def test_an_expired_cookie_says_so(logged_in, monkeypatch):
    # Age the cookie past the window rather than waiting a week for it.
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


def test_a_session_lasts_at_least_a_week_when_left_alone():
    assert SESSION_MAX_AGE >= 7 * 24 * 3600
    # Renewal has to happen well inside the window, or it never gets the
    # chance to run before the cookie it would renew has already expired.
    assert 0 < SESSION_RENEW_AFTER < SESSION_MAX_AGE


def test_a_fresh_cookie_is_not_reissued(logged_in):
    # One Set-Cookie per client per day, not one per request.
    assert "set-cookie" not in logged_in.get("/api/auth/me").headers


def test_using_the_app_pushes_the_expiry_back(logged_in, monkeypatch):
    # Treat every cookie as due for renewal rather than waiting a day.
    monkeypatch.setattr("app.auth.SESSION_RENEW_AFTER", -1)
    before = logged_in.cookies[COOKIE_NAME]

    # Signed timestamps have one-second resolution, so a reissue inside the
    # same second is byte-identical and would prove nothing about the window
    # having moved. Wait out the tick.
    time.sleep(1.1)

    resp = logged_in.get("/api/auth/me")
    assert resp.status_code == 200
    assert "set-cookie" in resp.headers, "an in-use session was not renewed"

    # The replacement is a genuinely newer cookie, and it works.
    after = logged_in.cookies[COOKIE_NAME]
    assert after != before
    assert _issued_at(after) > _issued_at(before), "the expiry did not move"
    assert logged_in.get("/api/auth/me").status_code == 200


def test_renewal_reaches_endpoints_that_return_a_response_directly(teacher_client, monkeypatch):
    """qr.svg returns a FileResponse, and those take a different path out.

    FastAPI merges a dependency's response headers only when the endpoint
    returns data to serialise, so setting the cookie in the dependency would
    be dropped here without any error — the projected view would quietly stop
    renewing while every JSON endpoint kept working.
    """
    monkeypatch.setattr("app.auth.SESSION_RENEW_AFTER", -1)
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]

    resp = teacher_client.get(f"/api/sessions/{code}/qr.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "set-cookie" in resp.headers, "a Response-returning endpoint did not renew"


def test_an_unauthenticated_request_is_not_given_a_cookie(client, monkeypatch):
    monkeypatch.setattr("app.auth.SESSION_RENEW_AFTER", -1)
    assert "set-cookie" not in client.get("/api/auth/me").headers
