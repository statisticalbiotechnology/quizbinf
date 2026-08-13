"""The Canvas course roster: who is enrolled, and how they map to a KTH id.

Personal data throughout — real students' names and usernames — so every
endpoint is teacher-only and none of it is safe to project.

The Canvas access token never leaves the server. Clients see whether Canvas is
configured, never the token itself.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import canvas, service
from ..auth import current_teacher
from ..config import Settings, get_settings
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/roster", tags=["roster"])


def _require_canvas(settings: Settings) -> None:
    if not settings.canvas_configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Canvas is not configured. Set CANVAS_TOKEN (a personal access token "
            "from <canvas>/profile/settings) in the app's configuration file.",
        )


@router.get("/status")
def roster_status(
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Whether Canvas can be reached, and what has been synced so far."""
    return {
        "canvas_configured": settings.canvas_configured,
        "canvas_base_url": settings.canvas_base_url,
        "courses": service.roster_courses(db, teacher),
    }


@router.get("/courses")
def teacher_courses(
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """Canvas courses the token's owner teaches, so one can be picked by name
    rather than by hunting for its numeric id."""
    _require_canvas(settings)
    try:
        return canvas.list_teacher_courses(settings.canvas_base_url, settings.canvas_token)
    except canvas.CanvasError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))


@router.post("/sync")
def sync(
    course_id: int = Query(...),
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Mirror a Canvas course's student list into the local roster."""
    _require_canvas(settings)
    try:
        students = canvas.list_course_students(
            settings.canvas_base_url, settings.canvas_token, course_id
        )
    except canvas.CanvasError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e))
    return service.sync_roster(db, teacher, course_id, students)


@router.get("")
def roster(
    course_id: int = Query(...),
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
) -> list[dict]:
    """The stored roster for a course. Names of real students — do not project."""
    return [
        {
            "canvas_user_id": e.canvas_user_id,
            "kthid": e.kthid,
            "username": e.username,
            "display_name": e.display_name,
        }
        for e in service.course_roster(db, course_id)
    ]
