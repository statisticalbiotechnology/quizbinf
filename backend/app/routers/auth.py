import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from .. import service
from ..auth import (
    DEVICE_COOKIE,
    clear_session_cookie,
    current_user,
    get_or_create_user,
    set_device_cookie,
    set_session_cookie,
)
# The same normalisation the roster sync uses. Shared deliberately: if login
# lower-cased differently from the sync, every match would silently fail.
from ..canvas import username_from_login_id as username_from_email
from ..config import Settings, get_settings
from ..db import get_db
from ..models import User
from ..schemas import MockLoginIn, RosterLoginIn, UserOut
from ..throttle import suggest_throttle, teacher_login_throttle

log = logging.getLogger("quizbinf")

router = APIRouter(prefix="/api/auth", tags=["auth"])


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
        "oidc": False,
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
        # Constant-time: a timing difference would leak the password prefix.
        if not secrets.compare_digest(body.password or "", settings.roster_teacher_password):
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
def oidc_login() -> None:
    """Placeholder for the KTH OIDC authorization-code flow.

    Implement once client registration with KTH IT is done (see CLAUDE.md).
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "KTH OIDC login is not configured yet; use mock login in development",
    )
