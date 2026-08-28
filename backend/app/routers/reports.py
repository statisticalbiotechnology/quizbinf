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

# The identifying block Canvas's own gradebook export writes, in its order.
#
# Every column here is load-bearing. Canvas validates the header by counting
# these columns and then requiring the **last** of them to be "Section"
# (`GradebookImporter#header?`); without it the import is refused outright with
# "The CSV header row is invalid", which is what happened when this file first
# went to a real Canvas. `Section` itself is never read back — the app does not
# know a student's section — so it goes out empty.
#
# `ID` and the two SIS columns are all sent because Canvas matches on its own
# user id first and falls back to the SIS ones, and a manually created course
# may have no SIS ids at all.
CANVAS_STUDENT_COLUMNS = ["Student", "ID", "SIS User ID", "SIS Login ID", "Section"]


def canvas_student_cells(row: dict) -> list:
    """The identifying cells for one student, matching CANVAS_STUDENT_COLUMNS."""
    return [
        row["name"],
        row["canvas_user_id"],
        row["sis_user_id"],
        row["username"],
        "",  # Section: required in the header, not something the app knows.
    ]


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
    threshold: float = Query(service.DEFAULT_ANSWER_THRESHOLD, ge=0.0, le=1.0),
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
) -> Response:
    """Attendance in the shape Canvas's gradebook importer reads.

    Canvas's own gradebook export is the format: an identifying block of
    columns, then one column per assignment, then a second row giving the
    points possible. A column whose name matches no existing assignment makes
    Canvas offer to create one on import, which is how a teacher turns this
    into a participation Assignment without setting anything up first.

    The mark is one point per lecture in which the student answered at least
    `threshold` of the bouts that ran — the closest this app gets to a record
    of who was in the room, since a login proves only that someone knows the
    session code.
    """
    course = course_id or settings.canvas_course_id
    report = service.canvas_participation(
        db, teacher, course, start, end, threshold
    )
    total = len(report["sessions"])

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CANVAS_STUDENT_COLUMNS + [assignment])
    # Canvas reads this row for the denominator, not as a student.
    writer.writerow(["    Points Possible"] + [""] * (len(CANVAS_STUDENT_COLUMNS) - 1) + [total])
    for row in report["students"]:
        writer.writerow(canvas_student_cells(row) + [row["attended"]])

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
