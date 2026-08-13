import enum
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    teacher = "teacher"
    student = "student"


class Phase(str, enum.Enum):
    pre = "pre"
    post = "post"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # KTH username, e.g. "lukask" — the stable user key.
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.student)


class RosterEntry(Base):
    """A student enrolled in a Canvas course, as of the last sync.

    Two jobs. It records who is *supposed* to be in the room, and it maps a
    Canvas user id to a KTH identity — which is what makes Canvas login usable
    without asking for extra API scopes on the developer key: the login itself
    returns only a Canvas user id, and this table turns that into a person.

    `kthid` (Canvas `sis_user_id`, a `u1…` value) is the identifier to match
    on. It outlives a username change, and it is the same identifier KTH's own
    IdP exposes — so a student who authenticates through Canvas today and
    through KTH tomorrow stays one person rather than becoming two.

    Personal data: names of real students. Kept to the minimum needed to
    identify them; the email Canvas also returns is deliberately not stored,
    because the app never sends mail.
    """

    __tablename__ = "roster_entries"
    __table_args__ = (
        UniqueConstraint("course_id", "canvas_user_id", name="uq_roster_course_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The Canvas course this enrolment belongs to.
    course_id: Mapped[int] = mapped_column(index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    canvas_user_id: Mapped[int] = mapped_column(index=True)
    # Canvas sis_user_id: KTH's permanent person id. May be absent for a
    # Canvas account with no SIS record, so it is nullable rather than assumed.
    kthid: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # Local part of Canvas login_id ("shiraza@kth.se" -> "shiraza").
    username: Mapped[str] = mapped_column(String(64), index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    owner: Mapped[User] = relationship()


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    owner: Mapped[User] = relationship()
    questions: Mapped[list["Question"]] = relationship(
        back_populates="quiz", order_by="Question.position", cascade="all, delete-orphan"
    )


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"))
    position: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)  # markdown allowed
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    quiz: Mapped[Quiz] = relationship(back_populates="questions")
    choices: Mapped[list["Choice"]] = relationship(
        back_populates="question", order_by="Choice.position", cascade="all, delete-orphan"
    )


class Choice(Base):
    __tablename__ = "choices"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    position: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)
    # Exactly one choice per question is correct; enforced at the API layer.
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False)

    question: Mapped[Question] = relationship(back_populates="choices")


def new_session_code() -> str:
    # Unambiguous lowercase alphabet for a code students may have to type.
    alphabet = "".join(c for c in string.ascii_lowercase + string.digits if c not in "l1o0")
    return "".join(secrets.choice(alphabet) for _ in range(6))


class QuizSession(Base):
    """A lecture run of a quiz; owns the short code in the QR URL."""

    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"))
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True, default=new_session_code)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    quiz: Mapped[Quiz] = relationship()
    rounds: Mapped[list["Round"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Round(Base):
    """One asking of a question in a session: question x phase (pre/post)."""

    __tablename__ = "rounds"
    __table_args__ = (
        UniqueConstraint("session_id", "question_id", "phase", name="uq_round_per_phase"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    phase: Mapped[Phase] = mapped_column(Enum(Phase))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    session: Mapped[QuizSession] = relationship(back_populates="rounds")
    question: Mapped[Question] = relationship()
    answers: Mapped[list["Answer"]] = relationship(
        back_populates="round", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.closed_at is None


class SessionParticipant(Base):
    """A student who has opened this session at least once.

    Answers alone cannot tell the teacher how many people are in the room
    before a round opens, which is what the projected join screen needs.
    """

    __tablename__ = "session_participants"
    __table_args__ = (
        UniqueConstraint("session_id", "user_id", name="uq_participant_per_session"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    session: Mapped[QuizSession] = relationship()
    user: Mapped[User] = relationship()


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        UniqueConstraint("round_id", "user_id", name="uq_one_answer_per_user_per_round"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    choice_id: Mapped[int] = mapped_column(ForeignKey("choices.id"))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    round: Mapped[Round] = relationship(back_populates="answers")
    user: Mapped[User] = relationship()
    choice: Mapped[Choice] = relationship()
