"""Round lifecycle and answering rules.

This module is the heart of the app: it enforces when rounds may open/close
and when answers are accepted. The server is the single source of truth for
whether a round is open — clients never decide based on their own clock.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Answer, Choice, Phase, Question, QuizSession, Round, User, utcnow


class RuleViolation(Exception):
    """A domain rule was violated; maps to HTTP 409 at the API layer."""


def open_round(db: Session, session: QuizSession, question: Question, phase: Phase) -> Round:
    """Open a round for `question` in `session`.

    Rules:
    - Only one round may be open per session at a time.
    - A round (question x phase) can be opened only once — no reopening.
    - The post round requires the pre round of the same question to be closed.
    """
    if question.quiz_id != session.quiz_id:
        raise RuleViolation("Question does not belong to this session's quiz")
    if get_open_round(db, session) is not None:
        raise RuleViolation("Another round is already open in this session")
    existing = db.scalar(
        select(Round).where(
            Round.session_id == session.id,
            Round.question_id == question.id,
            Round.phase == phase,
        )
    )
    if existing is not None:
        raise RuleViolation(f"The {phase.value} round for this question was already run")
    if phase == Phase.post:
        pre = db.scalar(
            select(Round).where(
                Round.session_id == session.id,
                Round.question_id == question.id,
                Round.phase == Phase.pre,
            )
        )
        if pre is None or pre.is_open:
            raise RuleViolation("The post round requires a closed pre round first")
    round_ = Round(session_id=session.id, question_id=question.id, phase=phase)
    db.add(round_)
    db.commit()
    db.refresh(round_)
    return round_


def close_round(db: Session, round_: Round) -> Round:
    if not round_.is_open:
        raise RuleViolation("Round is already closed")
    round_.closed_at = utcnow()
    db.commit()
    db.refresh(round_)
    return round_


def get_open_round(db: Session, session: QuizSession) -> Round | None:
    return db.scalar(
        select(Round).where(Round.session_id == session.id, Round.closed_at.is_(None))
    )


def submit_answer(db: Session, round_: Round, user: User, choice: Choice) -> Answer:
    """Record `user`'s answer; one answer per user per round, last write wins
    while the round is open."""
    if not round_.is_open:
        raise RuleViolation("This round is closed")
    if choice.question_id != round_.question_id:
        raise RuleViolation("Choice does not belong to the round's question")
    answer = db.scalar(
        select(Answer).where(Answer.round_id == round_.id, Answer.user_id == user.id)
    )
    if answer is None:
        answer = Answer(round_id=round_.id, user_id=user.id, choice_id=choice.id)
        db.add(answer)
    else:
        answer.choice_id = choice.id
        answer.submitted_at = utcnow()
    db.commit()
    db.refresh(answer)
    return answer


def round_histogram(db: Session, round_: Round) -> dict[int, int]:
    """Aggregate answer counts per choice id. Never exposes who answered what."""
    counts = {choice.id: 0 for choice in round_.question.choices}
    for answer in round_.answers:
        counts[answer.choice_id] = counts.get(answer.choice_id, 0) + 1
    return counts


def pre_post_comparison(db: Session, session: QuizSession, question: Question) -> dict:
    """Pre vs post answer distributions for one question in a session."""
    result: dict = {"question_id": question.id, "pre": None, "post": None}
    for round_ in session.rounds:
        if round_.question_id == question.id:
            result[round_.phase.value] = round_histogram(db, round_)
    return result
