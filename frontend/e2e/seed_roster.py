"""Put a small course roster into the e2e database.

The browser tests need something for roster login and its type-ahead to match
against, and there is no Canvas to sync from in that environment. Names are
invented; the shapes (a `u1…` kthid, a `name@kth.se` login) match what Canvas
actually returns.
"""

from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import RosterEntry, Role, User

STUDENTS = [
    ("shiraza", "Shiraz Abbas"),
    ("shirin", "Shirin Bergman"),
    ("ahmaa", "Ahmed Abdelmoez"),
    ("linaah2", "Lina Al-Hanbali"),
    ("sofiaali", "Sofia Ali"),
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    course_id = get_settings().canvas_course_id
    db = SessionLocal()

    owner = db.query(User).filter(User.username == "teacher").one_or_none()
    if owner is None:
        owner = User(username="teacher", display_name="Teacher", role=Role.teacher)
        db.add(owner)
        db.commit()
        db.refresh(owner)

    for i, (username, display_name) in enumerate(STUDENTS):
        db.add(
            RosterEntry(
                course_id=course_id,
                owner_id=owner.id,
                canvas_user_id=100000 + i,
                kthid=f"u1seed{i}",
                username=username,
                display_name=display_name,
            )
        )
    db.commit()
    db.close()
    print(f"seeded {len(STUDENTS)} roster entries for course {course_id}")


if __name__ == "__main__":
    main()
