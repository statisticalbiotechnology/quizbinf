"""Cross-session reporting, for the end of a term.

Personal data throughout: this says which named students turned up. Teacher
only, restricted to the teacher's own sessions, and never projected.
"""

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from .. import service
from ..auth import current_teacher
from ..config import Settings, get_settings
from ..db import get_db
from ..models import User

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/participation")
def semester_participation(
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
) -> dict:
    """Attendance per student across every session in the range."""
    return service.semester_participation(db, teacher, start, end)


@router.get("/canvas-participation.csv", include_in_schema=False)
def canvas_participation_csv(
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    settings: Settings = Depends(get_settings),
    course_id: int | None = Query(None),
    assignment: str = Query("Quiz participation"),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
) -> Response:
    """Attendance in the shape Canvas's gradebook importer reads.

    Canvas's own gradebook export is the format: an identifying block of
    columns, then one column per assignment, then a second row giving the
    points possible. A column whose name matches no existing assignment makes
    Canvas offer to create one on import, which is how a teacher turns this
    into a participation Assignment without setting anything up first.

    The mark is sessions attended out of sessions run, so it is the same
    number the plain report shows rather than a re-interpretation of it.
    """
    course = course_id or settings.canvas_course_id
    report = service.canvas_participation(db, teacher, course, start, end)
    total = len(report["sessions"])

    buf = io.StringIO()
    writer = csv.writer(buf)
    # The identifying columns Canvas's own gradebook export writes, in its
    # order. Canvas tries `ID` (its own user id) first and falls back to the
    # SIS columns, so all three go out: an import then works whether or not
    # the course has SIS ids.
    writer.writerow(["Student", "ID", "SIS User ID", "SIS Login ID", assignment])
    # Canvas reads this row for the denominator, not as a student.
    writer.writerow(["    Points Possible", "", "", "", total])
    for row in report["students"]:
        writer.writerow(
            [
                row["name"],
                row["canvas_user_id"],
                row["sis_user_id"],
                row["username"],
                row["attended"],
            ]
        )

    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="canvas-participation.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/participation.csv", include_in_schema=False)
def semester_participation_csv(
    db: Session = Depends(get_db),
    teacher: User = Depends(current_teacher),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
) -> Response:
    """The same, as a spreadsheet — one row per student, one column per session.

    Cells are yes/no: did the student answer both bouts of every question that
    was asked twice. A session where no question ran both bouts has nothing to
    attend and is left blank rather than counted as an absence.
    """
    report = service.semester_participation(db, teacher, start, end)
    sessions = report["sessions"]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["username", "name"]
        + [f"{s['date']} {s['title']} ({s['code']})" for s in sessions]
        + ["sessions_attended", "sessions_total"]
    )
    for row in report["students"]:
        cells = []
        for entry in row["sessions"]:
            if entry["took_part"] is None:
                cells.append("")
            elif entry["took_part"]:
                cells.append("yes")
            else:
                # Not just "no": show how far they got, so a student who
                # answered three of four questions is distinguishable from
                # one who never turned up.
                cells.append(f"no ({entry['completed']}/{entry['asked']})")
        writer.writerow(
            [row["username"], row["display_name"]] + cells + [row["attended"], len(sessions)]
        )

    span = ""
    if start or end:
        span = f"-{start or 'start'}_{end or 'end'}"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="quizbinf-participation{span}.csv"',
            "Cache-Control": "no-store",
        },
    )
