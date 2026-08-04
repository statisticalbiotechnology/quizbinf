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
