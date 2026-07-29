from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from ..auth import clear_session_cookie, current_user, get_or_create_user, set_session_cookie
from ..config import Settings, get_settings
from ..db import get_db
from ..models import User
from ..schemas import MockLoginIn, UserOut

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


@router.get("/login")
def oidc_login() -> None:
    """Placeholder for the KTH OIDC authorization-code flow.

    Implement once client registration with KTH IT is done (see CLAUDE.md).
    """
    raise HTTPException(
        status.HTTP_501_NOT_IMPLEMENTED,
        "KTH OIDC login is not configured yet; use mock login in development",
    )
