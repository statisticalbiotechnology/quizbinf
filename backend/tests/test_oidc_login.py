"""The KTH OIDC authorization-code flow.

KTH IT supports the `openid` and `allatclaims` scopes and recommends
"Authorization Code + client secret", which is what these pin. The provider
is stubbed: what is asserted is the request we send it, and what we do with
what it sends back.
"""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import oidc
from app.auth import FLOW_COOKIE
from app.config import get_settings

ISSUER = "https://login.kth.se"
CLIENT_ID = "quizbinf-client"
REDIRECT = "http://testserver/api/auth/callback"

DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/oidc/authorize",
    "token_endpoint": f"{ISSUER}/oidc/token",
}


def id_token(claims: dict) -> str:
    """An unsigned ID token; the flow trusts the back channel, not a signature."""
    def segment(payload: dict) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        return raw.rstrip("=")

    return f"{segment({'alg': 'RS256'})}.{segment(claims)}.signature"


def base_claims(**extra) -> dict:
    return {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "exp": time.time() + 300,
        "sub": "u1abcdef",
        **extra,
    }


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    oidc._discovery_cache.clear()
    yield
    oidc._discovery_cache.clear()


@pytest.fixture
def oidc_configured(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "oidc_issuer", ISSUER)
    monkeypatch.setattr(settings, "oidc_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "oidc_client_secret", "s3cret")
    return settings


@pytest.fixture
def provider(monkeypatch):
    """Stub the identity provider, recording what we sent it."""
    calls = {"token_request": None}

    def install(token_response, status_code=200):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("openid-configuration"):
                return httpx.Response(200, json=DISCOVERY)
            calls["token_request"] = dict(
                parse_qs(request.content.decode(), keep_blank_values=True)
            )
            return httpx.Response(status_code, json=token_response)

        real_client = httpx.Client

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(oidc.httpx, "Client", factory)
        return calls

    return install


def test_login_is_501_until_it_is_configured(client):
    resp = client.get("/api/auth/login", follow_redirects=False)
    assert resp.status_code == 501
    assert "OIDC_ISSUER" in resp.json()["detail"]


def test_login_redirects_to_the_provider(client, oidc_configured, provider):
    provider({})
    resp = client.get("/api/auth/login?next=/s/abc123", follow_redirects=False)

    assert resp.status_code == 307
    target = urlparse(resp.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == DISCOVERY["authorization_endpoint"]

    params = parse_qs(target.query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == [CLIENT_ID]
    assert params["redirect_uri"] == [REDIRECT]
    # The two scopes KTH supports, and no others.
    assert params["scope"] == ["openid allatclaims"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"]
    # The flow state rides in a signed cookie, not on the server.
    assert FLOW_COOKIE in resp.cookies


def test_pkce_can_be_switched_off(client, oidc_configured, provider, monkeypatch):
    """Kept configurable in case the provider rejects the extra parameters."""
    monkeypatch.setattr(oidc_configured, "oidc_use_pkce", False)
    provider({})
    resp = client.get("/api/auth/login", follow_redirects=False)

    params = parse_qs(urlparse(resp.headers["location"]).query)
    assert "code_challenge" not in params


def _start_flow(client, next_url="/s/abc123"):
    resp = client.get(f"/api/auth/login?next={next_url}", follow_redirects=False)
    return parse_qs(urlparse(resp.headers["location"]).query)["state"][0]


def test_a_successful_callback_signs_the_student_in(client, oidc_configured, provider):
    calls = provider({"id_token": id_token(base_claims(username="shiraza", name="Shiraz Abbas"))})
    state = _start_flow(client)

    resp = client.get(
        f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/s/abc123"

    # Exchanged over the back channel, with the secret and the PKCE verifier.
    assert calls["token_request"]["grant_type"] == ["authorization_code"]
    assert calls["token_request"]["client_secret"] == ["s3cret"]
    assert calls["token_request"]["code_verifier"]

    me = client.get("/api/auth/me").json()
    assert me["username"] == "shiraza"
    assert me["display_name"] == "Shiraz Abbas"


def test_the_teacher_allowlist_still_decides_the_role(client, oidc_configured, provider):
    """Role comes from configuration, never from anything the IdP says."""
    provider({"id_token": id_token(base_claims(username="teach", role="student"))})
    state = _start_flow(client, "/")
    client.get(f"/api/auth/callback?code=abc&state={state}", follow_redirects=False)

    assert client.get("/api/auth/me").json()["role"] == "teacher"


def test_a_mismatched_state_is_refused(client, oidc_configured, provider):
    provider({"id_token": id_token(base_claims(username="shiraza"))})
    _start_flow(client)

    resp = client.get("/api/auth/callback?code=abc&state=not-the-state")
    assert resp.status_code == 400
    assert client.get("/api/auth/me").status_code == 401


def test_a_callback_without_a_flow_cookie_is_refused(client, oidc_configured, provider):
    """What a bookmarked or replayed callback URL looks like."""
    provider({"id_token": id_token(base_claims(username="shiraza"))})
    resp = client.get("/api/auth/callback?code=abc&state=anything")
    assert resp.status_code == 400


def test_a_token_for_another_client_is_refused(client, oidc_configured, provider):
    provider({"id_token": id_token(base_claims(aud="some-other-app", username="shiraza"))})
    state = _start_flow(client)

    resp = client.get(f"/api/auth/callback?code=abc&state={state}")
    assert resp.status_code == 502
    assert "different application" in resp.json()["detail"]
    assert client.get("/api/auth/me").status_code == 401


def test_a_token_from_another_issuer_is_refused(client, oidc_configured, provider):
    provider({"id_token": id_token(base_claims(iss="https://evil.example", username="x"))})
    state = _start_flow(client)

    resp = client.get(f"/api/auth/callback?code=abc&state={state}")
    assert resp.status_code == 502
    assert "different provider" in resp.json()["detail"]


def test_an_expired_token_is_refused(client, oidc_configured, provider):
    provider({"id_token": id_token(base_claims(exp=time.time() - 10, username="shiraza"))})
    state = _start_flow(client)

    resp = client.get(f"/api/auth/callback?code=abc&state={state}")
    assert resp.status_code == 502
    assert "expired" in resp.json()["detail"]


def test_a_refused_exchange_is_reported_not_swallowed(client, oidc_configured, provider):
    provider({"error": "invalid_grant"}, status_code=400)
    state = _start_flow(client)

    resp = client.get(f"/api/auth/callback?code=stale&state={state}")
    assert resp.status_code == 502
    assert "invalid_grant" in resp.json()["detail"]


def test_the_provider_declining_returns_to_the_login_page(client, oidc_configured, provider):
    provider({})
    resp = client.get(
        "/api/auth/callback?error=access_denied&error_description=nope",
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?error=oidc"


def test_the_flow_cannot_redirect_off_site(client, oidc_configured, provider):
    """An open redirect would let a crafted login link bounce a student
    elsewhere looking as though quizbinf sent them."""
    provider({"id_token": id_token(base_claims(username="shiraza"))})
    for hostile in ("https://evil.example/phish", "//evil.example/phish"):
        state = _start_flow(client, hostile)
        resp = client.get(
            f"/api/auth/callback?code=abc&state={state}", follow_redirects=False
        )
        assert resp.headers["location"] == "/", hostile


def test_methods_reports_oidc_once_configured(client, oidc_configured):
    assert client.get("/api/auth/methods").json()["oidc"] is True


# --- claim handling --------------------------------------------------------


def test_the_username_claim_is_reduced_to_a_kth_username():
    claims = {"username": "Shiraza@kth.se"}
    assert oidc.username_from_claims(claims, "username") == "shiraza"


def test_other_spellings_of_the_username_claim_are_accepted():
    """`allatclaims` returns several; the preferred one wins, then fallbacks."""
    assert oidc.username_from_claims({"preferred_username": "ahmaa"}, "username") == "ahmaa"
    assert oidc.username_from_claims({"kthid": "u1abc"}, "username") == "u1abc"
    assert oidc.username_from_claims({"sub": "u1abc"}, "username") == "u1abc"
    assert oidc.username_from_claims({}, "username") is None


def test_discovery_is_cached(oidc_configured, provider):
    provider({})
    first = oidc.discover(ISSUER)
    # A second call must not need the provider at all.
    oidc._discovery_cache[ISSUER] = (oidc._discovery_cache[ISSUER][0], {"marker": True, **first})
    assert oidc.discover(ISSUER).get("marker") is True
