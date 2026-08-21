import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from starlette.responses import RedirectResponse

from .. import oidc, service
from ..auth import (
    DEVICE_COOKIE,
    clear_flow_cookie,
    clear_session_cookie,
    current_user,
    get_or_create_user,
    passwords_match,
    read_flow_cookie,
    set_device_cookie,
    set_flow_cookie,
    set_session_cookie,
)
# The same normalisation the roster sync uses. Shared deliberately: if login
# lower-cased differently from the sync, every match would silently fail.
from ..canvas import username_from_login_id as username_from_email
from ..config import Settings, get_settings
from ..db import get_db
from ..models import User
from ..public_base import public_base_url
from ..schemas import MockLoginIn, RosterLoginIn, UserOut
from ..throttle import suggest_throttle, teacher_login_throttle

log = logging.getLogger("quizbinf")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# The path registered with KTH IT. Changing it needs a form and a wait, so it
# is derived from the public base URL rather than configured separately —
# there is no way for the two to drift apart.
CALLBACK_PATH = "/api/auth/callback"


def _redirect_uri(request: Request, settings: Settings) -> str:
    return public_base_url(request, settings) + CALLBACK_PATH


def _safe_next(target: str) -> str:
    """Only ever return to a path on this site.

    An open redirect here would let a crafted login link bounce a student to
    another host carrying the impression that quizbinf sent them there.
    """
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    return target


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/logout")
def logout(response: Response) -> dict:
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/mock-login", response_model=UserOut)
def mock_login(
    body: MockLoginIn,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Development-only login that skips the IdP entirely.

    Hard-disabled unless MOCK_LOGIN=true and ENVIRONMENT != production.
    """
    if not settings.mock_login_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Mock login is disabled")
    user = get_or_create_user(db, body.username, body.display_name or body.username, settings)
    set_session_cookie(response, user.username, settings)
    return user


@router.get("/methods")
def login_methods(settings: Settings = Depends(get_settings)) -> dict:
    """Which ways of logging in this deployment offers.

    Lets the login page show the right form instead of guessing. Says nothing
    about *who* may log in, and carries no secret.
    """
    return {
        "mock_login": settings.mock_login_allowed,
        "roster_login": settings.roster_login_allowed,
        "oidc": settings.oidc_configured,
    }


@router.post("/roster-login", response_model=UserOut)
def roster_login(
    body: RosterLoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    """Identify a student against the synced course roster.

    This is identification, not authentication: a student types a KTH address
    and is let in if that address is enrolled. Anyone who knows a classmate's
    address could answer as them. It exists so the course can run before a
    real identity provider is available, and the submission window remains the
    thing that stops answering from outside the lecture.

    Teachers are the exception, because the teacher views hold every
    student's participation record. They need the shared password, and
    guessing it is rate-limited per client.
    """
    if not settings.roster_login_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Roster login is disabled")

    username = username_from_email(body.email)
    if not username:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Enter your KTH email address")

    if username in settings.teachers:
        client = request.client.host if request.client else "unknown"
        wait = teacher_login_throttle.locked_for(client)
        if wait:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Too many attempts. Try again in {wait} seconds.",
            )
        # Constant-time, and tolerant of a non-ASCII password — see
        # passwords_match, where both traps are documented.
        if not passwords_match(body.password, settings.roster_teacher_password):
            teacher_login_throttle.record_failure(client)
            log.warning("roster login: wrong teacher password for %s", username)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong teacher password")
        teacher_login_throttle.clear(client)
        user = get_or_create_user(db, username, username, settings)
        set_session_cookie(response, user.username, settings)
        return user

    # One device, one student identity, for a while. Without this a student
    # can sign in as each of their friends in turn on the same phone and
    # answer for all of them — the most obvious way to abuse a login that
    # asks for no proof.
    device_id = request.cookies.get(DEVICE_COOKIE) or ""
    held_by = service.device_claim_conflict(
        db, device_id, username, settings.device_binding_hours
    )
    if held_by is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This device has already been used to sign in as someone else "
            "today. Use your own phone or laptop, or ask the teacher.",
        )

    entry = service.roster_entry_for(db, username)
    if entry is None:
        # Deliberately the same answer whether the roster is empty or the
        # address simply is not on it: this endpoint must not become a way to
        # test who is enrolled on the course.
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "That address is not on the course roster. Use your KTH address, "
            "and ask the teacher if you have only just registered.",
        )
    user = get_or_create_user(db, entry.username, entry.display_name, settings)
    if not device_id:
        device_id = secrets.token_urlsafe(24)
        set_device_cookie(response, device_id, settings)
    service.record_device_claim(db, device_id, user.username)
    set_session_cookie(response, user.username, settings)
    return user


@router.get("/roster-suggest")
def roster_suggest(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Addresses on the course roster beginning with `q`, for the login field.

    Reachable without logging in, so it is deliberately grudging: nothing
    comes back until enough has been typed to be near-specific, matches are
    by prefix rather than substring, at most a handful are returned, and
    asking repeatedly is rate-limited. It still leaks the roster to anyone
    patient enough to try many prefixes — that is the cost of the convenience,
    and it is a smaller cost than a dropdown that hands the whole class over
    on page load.
    """
    if not settings.roster_login_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Roster login is disabled")

    client = request.client.host if request.client else "unknown"
    if suggest_throttle.locked_for(client):
        # Quietly empty rather than an error: the field should not break, and
        # a distinct response would tell a scraper it had been noticed.
        return {"matches": []}
    suggest_throttle.record(client)

    usernames = service.roster_suggestions(db, settings.canvas_course_id, q)
    return {"matches": [f"{u}@kth.se" for u in usernames]}


@router.get("/login")
def oidc_login(
    request: Request,
    next: str = "/",
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    """Start the KTH OIDC authorization-code flow.

    The state, the PKCE verifier and where to return to are carried in a
    short-lived signed cookie rather than server-side, so the flow survives a
    restart mid-login and needs no shared store if this ever runs on more
    than one replica.
    """
    if not settings.oidc_configured:
        raise HTTPException(
            status.HTTP_501_NOT_IMPLEMENTED,
            "OIDC login is not configured. Set OIDC_ISSUER, OIDC_CLIENT_ID and "
            "OIDC_CLIENT_SECRET once KTH IT has registered the application.",
        )
    try:
        document = oidc.discover(settings.oidc_issuer)
    except oidc.OidcError as e:
        log.error("oidc: discovery failed: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    state = secrets.token_urlsafe(24)
    verifier, challenge = oidc.make_pkce() if settings.oidc_use_pkce else (None, None)

    response = RedirectResponse(
        oidc.authorization_url(
            document,
            settings.oidc_client_id,
            _redirect_uri(request, settings),
            settings.oidc_scopes,
            state,
            challenge,
        ),
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )
    set_flow_cookie(
        response,
        {"state": state, "verifier": verifier, "next": _safe_next(next)},
        settings,
    )
    return response


@router.get("/callback")
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Where the provider sends the student back, signed in or not."""
    if not settings.oidc_configured:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "OIDC login is not configured")

    if error:
        # The provider declined; say so on the login page rather than showing
        # a raw error document.
        log.warning("oidc: provider returned %s: %s", error, error_description)
        return RedirectResponse("/login?error=oidc", status_code=status.HTTP_303_SEE_OTHER)

    flow = read_flow_cookie(request, settings)
    if flow is None or not state or not secrets.compare_digest(state, flow.get("state", "")):
        # A mismatched or missing state is the CSRF check doing its job, and
        # also what a bookmarked callback URL looks like.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Login session expired; try again")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No authorization code returned")

    try:
        document = oidc.discover(settings.oidc_issuer)
        tokens = oidc.exchange_code(
            document,
            code,
            _redirect_uri(request, settings),
            settings.oidc_client_id,
            settings.oidc_client_secret,
            flow.get("verifier"),
        )
    except oidc.OidcError as e:
        log.error("oidc: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    id_token = tokens.get("id_token")
    if not id_token:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "No ID token in the token response")
    try:
        claims = oidc.claims_from_id_token(
            id_token, settings.oidc_issuer, settings.oidc_client_id
        )
    except oidc.OidcError as e:
        log.error("oidc: %s", e)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))

    username = oidc.username_from_claims(claims, settings.oidc_username_claim)
    if not username:
        log.error("oidc: no username claim; got %s", sorted(claims))
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "The identity provider returned no username claim",
        )

    # The roster knows this person's name already; the IdP's is a fallback.
    entry = service.roster_entry_for(db, username)
    display_name = oidc.display_name_from_claims(
        claims, entry.display_name if entry else username
    )
    user = get_or_create_user(db, username, display_name, settings)

    response = RedirectResponse(
        flow.get("next") or "/", status_code=status.HTTP_303_SEE_OTHER
    )
    set_session_cookie(response, user.username, settings)
    clear_flow_cookie(response)
    return response
