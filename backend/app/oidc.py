"""The KTH OpenID Connect authorization-code flow.

KTH IT supports the `openid` and `allatclaims` scopes and recommends
"OIDC Authorization Code + client secret", which is what this implements.
Nothing here is KTH-specific beyond those defaults: any standards-compliant
provider works by pointing `OIDC_ISSUER` at it.

The client secret never leaves the server — the browser only ever sees a
redirect to the provider and a redirect back.
"""

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx

log = logging.getLogger("quizbinf")

TIMEOUT = httpx.Timeout(15.0)
# How long a login may sit half-finished at the provider before the state
# cookie is refused. Long enough to type a password, short enough that a
# stale one cannot be replayed later.
FLOW_MAX_AGE = 600


class OidcError(Exception):
    """The provider could not be reached, or refused the exchange."""


_discovery_cache: dict[str, tuple[float, dict]] = {}
DISCOVERY_TTL = 3600


def discover(issuer: str) -> dict:
    """The provider's endpoints, from its well-known document.

    Cached: the document changes rarely, and a lecture theatre full of
    students logging in at once should not each trigger a fetch.
    """
    now = time.time()
    cached = _discovery_cache.get(issuer)
    if cached and now - cached[0] < DISCOVERY_TTL:
        return cached[1]

    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise OidcError(f"Could not reach the identity provider: {exc}") from exc
    if response.status_code != 200:
        raise OidcError(f"Identity provider returned {response.status_code} for {url}")
    try:
        document = response.json()
    except ValueError as exc:
        raise OidcError("Identity provider returned a malformed discovery document") from exc

    for required in ("authorization_endpoint", "token_endpoint"):
        if not document.get(required):
            raise OidcError(f"Discovery document has no {required}")

    _discovery_cache[issuer] = (now, document)
    return document


def make_pkce() -> tuple[str, str]:
    """A PKCE verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def authorization_url(
    document: dict,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    code_challenge: str | None,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
    }
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    separator = "&" if "?" in document["authorization_endpoint"] else "?"
    return f"{document['authorization_endpoint']}{separator}{urlencode(params)}"


def exchange_code(
    document: dict,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    code_verifier: str | None,
) -> dict:
    """Trade the authorization code for tokens, over the back channel."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
            response = client.post(document["token_endpoint"], data=data)
    except httpx.HTTPError as exc:
        raise OidcError(f"Could not reach the identity provider: {exc}") from exc
    if response.status_code != 200:
        # The body names the OAuth error ("invalid_grant" and friends), which
        # is the difference between a misconfigured secret and a stale code.
        raise OidcError(
            f"Token exchange failed ({response.status_code}): {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError as exc:
        raise OidcError("Identity provider returned a malformed token response") from exc


def _decode_segment(segment: str) -> dict:
    padding = "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(segment + padding))


def claims_from_id_token(id_token: str, issuer: str, client_id: str) -> dict:
    """The ID token's claims, with the checks that matter here.

    The signature is deliberately not verified. OIDC Core §3.1.3.7 allows
    that when the token arrives over a TLS-protected back channel directly
    from the token endpoint, authenticated with the client secret — which is
    exactly this flow. It saves a JWKS fetch and a crypto dependency on the
    critical path. Verifying it properly is the obvious hardening step if
    this ever accepts tokens from anywhere else.

    Issuer, audience and expiry *are* checked, since those are what stop a
    token minted for another client or another provider being accepted.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OidcError("Identity provider returned a malformed ID token")
    try:
        claims = _decode_segment(parts[1])
    except (ValueError, TypeError) as exc:
        raise OidcError("Could not read the ID token") from exc

    if claims.get("iss", "").rstrip("/") != issuer.rstrip("/"):
        raise OidcError("ID token was issued by a different provider")

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise OidcError("ID token was issued for a different application")

    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or expires < time.time():
        raise OidcError("ID token has expired")

    return claims


# Spellings seen across providers, in the order they are preferred. KTH's
# `allatclaims` returns several of these at once.
USERNAME_CLAIMS = ("username", "preferred_username", "kthid", "sub")


def username_from_claims(claims: dict, preferred: str) -> str | None:
    """The KTH username, as the stable key the rest of the app uses."""
    for name in (preferred, *USERNAME_CLAIMS):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            # An address may arrive where a bare username was expected.
            return value.split("@", 1)[0].strip().lower()
    return None


def display_name_from_claims(claims: dict, fallback: str) -> str:
    for name in ("name", "displayName", "given_name"):
        value = claims.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback
