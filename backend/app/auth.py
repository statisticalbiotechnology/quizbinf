from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import Role, User

COOKIE_NAME = "quizbinf_session"

# How long a cookie stays valid without being used. Renewal below means this
# is an *idle* timeout: someone who keeps using the app is never signed out,
# so a session cannot lapse in the middle of a lecture.
SESSION_MAX_AGE = 7 * 24 * 3600  # a week away from the app

# Re-issue a cookie once it is older than this. Well short of the window, so
# an active session is always far from expiring, and rare enough that it costs
# one Set-Cookie per client per day rather than one per request.
SESSION_RENEW_AFTER = 24 * 3600

# Where current_user records that the cookie it accepted is due for renewal.
# A dependency cannot set the cookie itself: FastAPI merges a dependency's
# response headers only when the endpoint returns data to serialise, so
# anything returning a Response directly — qr.svg, the SPA fallback — would
# silently drop it. The middleware in main.py applies this to every response.
RENEW_FLAG = "renew_session_for"


def _serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.resolved_session_secret, salt="quizbinf-auth")


def set_session_cookie(response: Response, username: str, settings: Settings) -> None:
    token = _serializer(settings).dumps({"username": username})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME)


def get_or_create_user(db: Session, username: str, display_name: str, settings: Settings) -> User:
    role = Role.teacher if username in settings.teachers else Role.student
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, display_name=display_name, role=role)
        db.add(user)
    else:
        # The teacher allowlist in config is authoritative on every login.
        user.role = role
        if display_name:
            user.display_name = display_name
    db.commit()
    db.refresh(user)
    return user


def current_user(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    try:
        data, issued_at = _serializer(settings).loads(
            token, max_age=SESSION_MAX_AGE, return_timestamp=True
        )
    except SignatureExpired:
        # Ordinary and expected after SESSION_MAX_AGE. Reported separately
        # because SignatureExpired subclasses BadSignature, so folding the two
        # together makes a routine expiry look like a forged cookie — which
        # sent us hunting a session-secret bug that did not exist.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    except BadSignature:
        # Signed with a different secret, or tampered with.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
    user = db.scalar(select(User).where(User.username == data["username"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Unknown user")

    # Push the expiry back while the session is in use, so the window measures
    # time away from the app rather than time since logging in.
    if datetime.now(timezone.utc) - issued_at > timedelta(seconds=SESSION_RENEW_AFTER):
        setattr(request.state, RENEW_FLAG, user.username)
    return user


def current_teacher(user: User = Depends(current_user)) -> User:
    if user.role != Role.teacher:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Teacher role required")
    return user
