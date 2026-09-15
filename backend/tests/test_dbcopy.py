"""Moving every row from one database into another.

One implementation, two callers: the migration off SQLite onto a PostgreSQL
server, and `GET /api/backup.zip` on a deployment whose database *is*
PostgreSQL — where `VACUUM INTO` has nothing to work on and the archive would
otherwise be refused, which is to say the one button that rescues this app's
data would have disappeared the day the database moved.

These run SQLite to SQLite so they need no server. The PostgreSQL-specific
half — resetting identity sequences past the ids that were copied — is
verified against a real PostgreSQL 16 by hand; what is pinned here is that it
does nothing on a backend that has no such sequences.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, func, select

from app import models as m
from app.db import Base
from app.dbcopy import CopyRefused, copy_database


def _populate(url: str) -> None:
    engine = create_engine(url)
    Base.metadata.create_all(bind=engine)
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        teacher = m.User(username="lukask", display_name="L", role=m.Role.teacher)
        student = m.User(username="anna", display_name="A", role=m.Role.student)
        db.add_all([teacher, student])
        db.flush()
        quiz = m.Quiz(title="CB2442", owner_id=teacher.id)
        db.add(quiz)
        db.flush()
        question = m.Question(quiz_id=quiz.id, position=0, text="Why?")
        db.add(question)
        db.flush()
        choice = m.Choice(question_id=question.id, position=0, text="a", is_correct=True)
        db.add(choice)
        db.flush()
        session = m.QuizSession(quiz_id=quiz.id)
        db.add(session)
        db.flush()
        round_ = m.Round(session_id=session.id, question_id=question.id, phase=m.Phase.pre)
        db.add(round_)
        db.flush()
        db.add(m.Answer(round_id=round_.id, user_id=student.id, choice_id=choice.id))
        db.add(m.SessionParticipant(session_id=session.id, user_id=student.id))
        db.commit()
    engine.dispose()


def test_every_row_arrives_and_both_ends_agree(tmp_path):
    source = f"sqlite:///{tmp_path}/source.db"
    target = f"sqlite:///{tmp_path}/target.db"
    _populate(source)

    moved = copy_database(source, target)

    assert moved["tables"]["answers"] == 1
    assert moved["tables"]["users"] == 2
    assert moved["rows"] == sum(moved["tables"].values())

    # Not merely that the copy reported success — read the answer back out.
    connection = sqlite3.connect(f"{tmp_path}/target.db")
    try:
        found = connection.execute(
            "SELECT count(*) FROM answers JOIN users ON users.id = answers.user_id "
            "WHERE users.username = 'anna'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert found == 1, "the copy lost the student's answer"


def test_primary_keys_are_preserved(tmp_path):
    """Answers point at round ids and rounds at question ids.

    Renumbering during the copy would either break those references or require
    rewriting every one of them, so the ids travel unchanged — which is
    exactly why PostgreSQL's sequences have to be moved past them afterwards.
    """
    source = f"sqlite:///{tmp_path}/source.db"
    target = f"sqlite:///{tmp_path}/target.db"
    _populate(source)
    copy_database(source, target)

    for url in (source, target):
        engine = create_engine(url)
        with engine.connect() as connection:
            rows = connection.execute(
                select(m.Answer.id, m.Answer.round_id, m.Answer.user_id)
            ).all()
        engine.dispose()
        if url == source:
            original = rows
    assert rows == original, "ids changed in the copy, so references would dangle"


def test_copying_into_a_database_that_holds_rows_is_refused(tmp_path):
    """It would merge two datasets on colliding ids, and cannot be undone."""
    source = f"sqlite:///{tmp_path}/source.db"
    target = f"sqlite:///{tmp_path}/target.db"
    _populate(source)
    _populate(target)

    with pytest.raises(CopyRefused) as refused:
        copy_database(source, target)
    assert "already holds rows" in str(refused.value)


def test_copying_a_database_onto_itself_is_refused(tmp_path):
    source = f"sqlite:///{tmp_path}/source.db"
    _populate(source)
    with pytest.raises(CopyRefused):
        copy_database(source, source)


def test_an_empty_source_copies_cleanly(tmp_path):
    """A fresh deployment has a schema and no rows; that is not an error."""
    source = f"sqlite:///{tmp_path}/source.db"
    target = f"sqlite:///{tmp_path}/target.db"
    create_engine(source)
    Base.metadata.create_all(bind=create_engine(source))

    moved = copy_database(source, target)
    assert moved["rows"] == 0


def test_the_counts_are_read_back_rather_than_trusted(tmp_path, monkeypatch):
    """A copy that silently moved nothing looks like success from the inside.

    So the check is a count at both ends afterwards, not the running total the
    loop kept — those come from the same place and would agree with each other
    while disagreeing with the database.
    """
    source = f"sqlite:///{tmp_path}/source.db"
    target = f"sqlite:///{tmp_path}/target.db"
    _populate(source)

    real = copy_database

    # Make the target lose a row after the copy, and require the verification
    # to notice rather than the loop's own arithmetic.
    import app.dbcopy as dbcopy

    original_counts = dbcopy._row_counts

    def short_by_one(engine):
        counts = original_counts(engine)
        # Only once the target actually holds rows, so the emptiness check
        # that runs first still sees a genuinely empty database.
        if "target" in str(engine.url.database or "") and counts["answers"] > 0:
            counts["answers"] -= 1
        return counts

    monkeypatch.setattr(dbcopy, "_row_counts", short_by_one)
    with pytest.raises(CopyRefused) as refused:
        real(source, target)
    assert "disagree" in str(refused.value)


def test_sequence_reset_does_nothing_without_sequences(tmp_path):
    """SQLite has no identity sequences, so this must be a no-op rather than
    an error — the same code path runs for both backends."""
    from app.dbcopy import _reset_sequences

    engine = create_engine(f"sqlite:///{tmp_path}/x.db")
    Base.metadata.create_all(bind=engine)
    _reset_sequences(engine)  # must not raise
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(m.User.__table__)) == 0
    engine.dispose()
