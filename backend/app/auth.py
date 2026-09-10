import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db, writing
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


# Identifies a browser, never a person: an opaque random value, with nothing
# derived from the device itself. Deliberately outlives the session cookie, so
# signing out does not release the device to claim a different identity.
DEVICE_COOKIE = "quizbinf_device"
DEVICE_COOKIE_MAX_AGE = 180 * 24 * 3600


def set_device_cookie(response: Response, device_id: str, settings: Settings) -> None:
    response.set_cookie(
        DEVICE_COOKIE,
        device_id,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
    )


# Carries the in-progress OIDC login: the CSRF state, the PKCE verifier and
# where to return to. Signed rather than stored server-side, so a login
# survives a restart and needs no shared state across replicas.
FLOW_COOKIE = "quizbinf_oidc"
FLOW_MAX_AGE = 600


def _flow_serializer(settings: Settings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.resolved_session_secret, salt="quizbinf-oidc")


def set_flow_cookie(response: Response, flow: dict, settings: Settings) -> None:
    response.set_cookie(
        FLOW_COOKIE,
        _flow_serializer(settings).dumps(flow),
        max_age=FLOW_MAX_AGE,
        httponly=True,
        # The provider redirects the browser back to us, which is a top-level
        # cross-site navigation — "strict" would drop the cookie exactly then.
        samesite="lax",
        secure=settings.environment == "production",
    )


def read_flow_cookie(request: Request, settings: Settings) -> dict | None:
    token = request.cookies.get(FLOW_COOKIE)
    if not token:
        return None
    try:
        return _flow_serializer(settings).loads(token, max_age=FLOW_MAX_AGE)
    except BadSignature:
        return None


def clear_flow_cookie(response: Response) -> None:
    response.delete_cookie(FLOW_COOKIE)


def passwords_match(supplied: str | None, configured: str | None) -> bool:
    """Constant-time password comparison that survives a non-ASCII password.

    Two separate traps, both of which bite a Swedish password:

    `secrets.compare_digest` refuses `str` containing non-ASCII outright, so
    a password with "ä" raised TypeError and the request failed with a 500
    before any comparison happened. Comparing UTF-8 *bytes* works.

    And "ä" has two Unicode spellings — one code point (NFC) or "a" plus a
    combining diaeresis (NFD). macOS often produces NFD while a file written
    on Linux holds NFC, so the same character typed and stored can differ
    byte for byte. Normalising both sides first makes them agree.
    """
    return secrets.compare_digest(
        unicodedata.normalize("NFC", supplied or "").encode("utf-8"),
        unicodedata.normalize("NFC", configured or "").encode("utf-8"),
    )


def _already_current(user: User | None, display_name: str, role: Role) -> bool:
    """Is this row already what the login would write?"""
    return (
        user is not None
        and user.role == role
        and (not display_name or user.display_name == display_name)
    )


def get_or_create_user(db: Session, username: str, display_name: str, settings: Settings) -> User:
    """Sign in `username`, creating or correcting the row only if it needs it.

    Deliberately not a blanket `@write_path`, for the reason `record_participant`
    is not one either — and this is the more expensive of the two mistakes.

    Every student's arrival goes through here, so a class arriving together is
    150 calls inside a few seconds. The first version wrote unconditionally:
    it re-set `role` to the value it already held and committed, so *every*
    login took the process-wide write lock even when there was nothing to
    write. Against the deployment that was fatal — 185 of 200 logins refused
    after waiting the full `WRITE_QUEUE_SECONDS`, with 8 students in the
    session and the connection pool sitting at 15 of 50. Nothing was
    contended except the lock this function took for no reason.

    A returning student needs no write at all: the row is already right, so
    the read is the whole of the work and it runs concurrently with every
    other read. Only a genuinely new or changed row queues.

    The re-read inside the write transaction is not redundant. The check above
    happens outside the lock, so another request may have created the row in
    between — and under SQLite that is a losing INSERT rather than a merged
    one.
    """
    role = Role.teacher if username in settings.teachers else Role.student
    user = db.scalar(select(User).where(User.username == username))
    if _already_current(user, display_name, role):
        # End the read transaction: a Session holding one keeps a pooled
        # connection checked out, and skipping the write must not turn into
        # holding a connection instead.
        db.commit()
        return user

    db.commit()  # a deferred transaction cannot be promoted; close it first
    with writing(db):
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(username=username, display_name=display_name, role=role)
            db.add(user)
        else:
            # The teacher allowlist in config is authoritative on every login.
            user.role = role
            if display_name:
                user.display_name = display_name
        # No `refresh` afterwards. The commit populates a new row's primary
        # key, and the sessionmaker sets `expire_on_commit=False` so every
        # attribute stays readable — so a refresh was a second transaction,
        # taken while still holding the write lock, to re-read what was
        # already in hand.
        db.commit()
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

    # Hand the pooled connection back before returning.
    #
    # The SELECT above opened a transaction, and a Session in a transaction
    # holds a connection until something ends it. Dependencies and endpoints
    # are two separate hops through the thread pool, so without this the
    # connection is held across the wait for a *second* thread — by every
    # request in flight, whether or not it is doing anything. A class opening
    # the app together therefore wanted one connection each while forty
    # threads did the work, and the pool ran dry: the requests that had
    # already been accepted were the ones starving each other.
    #
    # `commit` rather than `rollback`, because this session is configured with
    # `expire_on_commit=False`: the attributes of `user` stay loaded, so the
    # endpoint reads them without going back to the database. There is nothing
    # to write here — the commit is only how a read-only transaction is ended.
    db.commit()
    return user


def current_teacher(user: User = Depends(current_user)) -> User:
    if user.role != Role.teacher:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Teacher role required")
    return user
