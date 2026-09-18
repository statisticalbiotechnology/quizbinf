"""What holds when writers are no longer taken one at a time.

On Postgres `writing()` does not serialise, so every read-then-write in the
app runs beside itself. The deployment said so before this file existed: a
lecture logged one `duplicate key ... uq_participant_per_session` per student
joining — a race the join path already tolerated, in a process that had been
built on the assumption it could not happen.

The tests below are of two kinds. The first are deterministic and run on
SQLite in CI: each drives the losing side of a race directly, without needing
two threads to collide. The last one is the real thing — threads against a
real Postgres — and is skipped unless `QUIZBINF_TEST_POSTGRES_URL` names a
database it may create tables in and write to.
"""

import os
import threading

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import auth, service
from app.db import Base, SessionLocal, insert_ignoring_conflict, writing
from app import db as db_module
from app.models import Answer, Choice, Phase, Question, Quiz, QuizSession, Role, Round
from app.models import SessionParticipant, User


def make_lecture(db, title="race"):
    """A quiz with one question, and a session of it. Built through the ORM
    rather than the API, because these tests are about what two callers of the
    service layer do to each other."""
    with writing(db):
        teacher = auth._create_user(db, f"teacher-{title}", "T", Role.teacher)
        quiz = Quiz(title=title, owner_id=teacher.id)
        db.add(quiz)
        db.commit()
        question = Question(quiz_id=quiz.id, position=0, text="q")
        question.choices = [
            Choice(position=0, text="a", is_correct=True),
            Choice(position=1, text="b", is_correct=False),
        ]
        db.add(question)
        db.commit()
        session = QuizSession(quiz_id=quiz.id)
        db.add(session)
        db.commit()
    return session, question


# --- the losing side of each race, driven directly ------------------------


def test_an_answer_is_refused_if_the_round_closed_while_it_waited(client):
    """The submission window is the attendance guard, so it must hold to the
    commit and not merely to the check the caller made on the way in.

    The caller loads the round, then queues — for the write gate on SQLite,
    for a row lock on Postgres. Both can take long enough for the teacher to
    halt the round in between, and the stale object in hand still says open.
    """
    db = SessionLocal()
    session, question = make_lecture(db, "stale")
    round_ = service.open_round(db, session, question, Phase.pre)
    student = auth.get_or_create_user(db, "stale-round", "Stale", _settings())

    closer = SessionLocal()
    service.close_round(closer, closer.get(Round, round_.id))
    closer.close()

    with pytest.raises(service.RuleViolation):
        service.submit_answer(db, round_, student, question.choices[0])
    assert db.scalar(select(func.count()).select_from(Answer).where(Answer.round_id == round_.id)) == 0
    db.close()


def test_a_second_participant_row_is_not_an_error(client):
    """`record_participant` wants the row to exist, not to be the one that
    wrote it. Losing to the student's own other request is success."""
    db = SessionLocal()
    session, _ = make_lecture(db, "twice")
    user = auth.get_or_create_user(db, "twice", "Twice", _settings())

    for _ in range(2):
        with writing(db):
            insert_ignoring_conflict(
                db,
                SessionParticipant,
                session_id=session.id,
                user_id=user.id,
                joined_at=service.utcnow(),
                last_seen_at=service.utcnow(),
            )
            db.commit()

    rows = db.scalar(
        select(func.count())
        .select_from(SessionParticipant)
        .where(SessionParticipant.session_id == session.id)
    )
    assert rows == 1
    db.close()


def test_a_login_that_loses_the_insert_reads_the_winner_s_row(client):
    """First login of a lecture: the page and the live stream both arrive with
    no row yet, and the unique index on `username` refuses one of them. The
    loser must return the row the winner wrote, not a 500 while a class signs
    in."""
    db = SessionLocal()
    winner = auth.get_or_create_user(db, "raced", "Raced", _settings())
    # _create_user is the loser's path: it inserts unconditionally, so calling
    # it when the row already exists is exactly what losing feels like.
    with writing(db):
        loser = auth._create_user(db, "raced", "Raced", Role.student)
    assert loser.id == winner.id
    assert db.scalar(select(func.count()).select_from(User).where(User.username == "raced")) == 1
    db.close()


def _settings():
    from app.config import get_settings

    return get_settings()


# --- the real thing, against Postgres -------------------------------------

POSTGRES_URL = os.environ.get("QUIZBINF_TEST_POSTGRES_URL")


@pytest.mark.skipif(not POSTGRES_URL, reason="set QUIZBINF_TEST_POSTGRES_URL to run")
def test_a_class_arriving_and_answering_at_once_on_postgres():
    """Threads, a real Postgres, and the gate off — the deployment's shape.

    Asserts what the gate used to: every student ends with one participant row
    and one answer, one round opens however many clicks arrive, and no request
    fails with anything but the refusals the rules define.
    """
    engine = create_engine(POSTGRES_URL)
    Base.metadata.create_all(engine)
    Sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    was = db_module._serialise_writes
    db_module._serialise_writes = False  # what a Postgres deployment does
    students = 40
    try:
        setup = Sessions()
        session, question = make_lecture(setup, f"pg{os.getpid()}")
        choice_id = question.choices[0].id

        errors: list[BaseException] = []
        opened: list[Round] = []

        def open_it():
            db = Sessions()
            try:
                opened.append(service.open_round(db, db.get(QuizSession, session.id),
                                                 db.get(Question, question.id), Phase.pre))
            except service.RuleViolation:
                pass  # one click wins, the rest are told so
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
            finally:
                db.close()

        # Several clicks on "open", as a double tap or two teacher screens.
        run_together([open_it] * 4)
        assert len(opened) == 1, "two rounds open at once breaks every later read"

        def arrive(i: int):
            """One student: the page and the live stream, together, twice."""
            db = Sessions()
            try:
                user = auth.get_or_create_user(db, f"s{i}", f"S{i}", _settings())
                quiz_session = db.get(QuizSession, session.id)
                service.record_participant(db, quiz_session, user)
                round_ = db.get(Round, opened[0].id)
                service.submit_answer(db, round_, user, db.get(Choice, choice_id))
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
            finally:
                db.close()

        # Each student twice at once: first login, join and answer all race
        # with the student's own other request.
        run_together([lambda i=i: arrive(i) for i in range(students) for _ in range(2)])
        assert not errors, errors[:3]

        check = Sessions()
        counts = {
            "users": check.scalar(select(func.count()).select_from(User).where(User.username.like("s%"))),
            "participants": check.scalar(
                select(func.count()).select_from(SessionParticipant).where(
                    SessionParticipant.session_id == session.id)),
            "answers": check.scalar(
                select(func.count()).select_from(Answer).where(Answer.round_id == opened[0].id)),
        }
        assert counts == {"users": students, "participants": students, "answers": students}

        # And the window closes for good: after the round is closed, a late
        # answer is refused rather than recorded.
        service.close_round(check, check.get(Round, opened[0].id))
        late = Sessions()
        latecomer = late.scalar(select(User).where(User.username == "s0"))
        with pytest.raises(service.RuleViolation):
            service.submit_answer(late, late.get(Round, opened[0].id),
                                  latecomer, late.get(Choice, choice_id))
        late.close()
        check.close()
    finally:
        db_module._serialise_writes = was
        Base.metadata.drop_all(engine)
        engine.dispose()


def run_together(calls):
    """Start every call at once and wait for all of them."""
    ready = threading.Barrier(len(calls))

    def go(call):
        ready.wait()
        call()

    threads = [threading.Thread(target=go, args=(c,)) for c in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
