"""Round lifecycle and answering rules.

This module is the heart of the app: it enforces when rounds may open/close
and when answers are accepted. The server is the single source of truth for
whether a round is open — clients never decide based on their own clock.
"""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Answer,
    Choice,
    Phase,
    Question,
    QuizSession,
    Round,
    SessionParticipant,
    User,
    utcnow,
)


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


def record_participant(db: Session, session: QuizSession, user: User) -> None:
    """Note that `user` has the session open. Idempotent; safe to call often."""
    participant = db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session.id,
            SessionParticipant.user_id == user.id,
        )
    )
    if participant is not None:
        participant.last_seen_at = utcnow()
        db.commit()
        return

    db.add(SessionParticipant(session_id=session.id, user_id=user.id))
    try:
        db.commit()
    except IntegrityError:
        # A student's first load fetches the state and opens the SSE stream at
        # almost the same moment, so both requests can find no row and try to
        # insert one. Losing that race is not an error — the row exists — but
        # letting it raise would 500 exactly when a student joins.
        db.rollback()


def participant_count(db: Session, session: QuizSession) -> int:
    """How many distinct people have opened this session."""
    return (
        db.scalar(
            select(func.count())
            .select_from(SessionParticipant)
            .where(SessionParticipant.session_id == session.id)
        )
        or 0
    )


def participation_report(db: Session, session: QuizSession) -> list[dict]:
    """Per-student answers for a session, for the teacher only.

    This is personal data, unlike every other view in the app: it says who
    answered what. It exists because the quiz is formative — the teacher wants
    to see who is following along — so it reports correctness per question and
    nothing else about the student beyond their username and name.

    Rows are sorted by name so the table is stable between reloads.
    """
    rounds = sorted(session.rounds, key=lambda r: (r.question_id, r.phase.value))
    correct_choice: dict[int, int | None] = {}
    for round_ in rounds:
        if round_.question_id not in correct_choice:
            correct = next((c for c in round_.question.choices if c.is_correct), None)
            correct_choice[round_.question_id] = correct.id if correct else None

    # user_id -> question_id -> phase -> chosen choice id
    chosen: dict[int, dict[int, dict[str, int]]] = {}
    users: dict[int, User] = {}
    for round_ in rounds:
        for answer in round_.answers:
            users[answer.user_id] = answer.user
            chosen.setdefault(answer.user_id, {}).setdefault(round_.question_id, {})[
                round_.phase.value
            ] = answer.choice_id

    # Anyone who opened the session counts, even if they never answered:
    # "joined but silent" is exactly what a teacher wants to notice.
    for participant in db.scalars(
        select(SessionParticipant).where(SessionParticipant.session_id == session.id)
    ):
        users.setdefault(participant.user_id, participant.user)

    question_ids = [q.id for q in session.quiz.questions]

    rows: list[dict] = []
    for user_id, user in users.items():
        per_question = []
        pre_correct = post_correct = answered = 0
        for question_id in question_ids:
            picks = chosen.get(user_id, {}).get(question_id, {})
            right = correct_choice.get(question_id)

            def verdict(phase: str) -> bool | None:
                if phase not in picks:
                    return None
                return right is not None and picks[phase] == right

            pre, post = verdict("pre"), verdict("post")
            answered += sum(1 for v in (pre, post) if v is not None)
            pre_correct += 1 if pre else 0
            post_correct += 1 if post else 0
            per_question.append({"question_id": question_id, "pre": pre, "post": post})
        rows.append(
            {
                "username": user.username,
                "display_name": user.display_name,
                "answers": per_question,
                "answered": answered,
                "pre_correct": pre_correct,
                "post_correct": post_correct,
            }
        )
    rows.sort(key=lambda r: (r["display_name"].lower(), r["username"]))
    return rows


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
