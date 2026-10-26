import enum
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    false,
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


class DeviceClaim(Base):
    """Which student identity a browser has claimed, and when.

    Roster identification has one obvious hole: nothing stops a student
    signing in as three classmates in turn and answering for all of them. A
    device that has claimed one identity is held to it for a while, which
    closes that hole without preventing anything legitimate — a student
    answers from their own phone as themselves.

    It does not stop a *different* device claiming someone else's address.
    Only real authentication does that.

    The id is an opaque random value in a cookie. It identifies a browser,
    never a person: there is no fingerprinting here, and nothing about the
    device is recorded beyond this identifier.
    """

    __tablename__ = "device_claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Quiz(Base):
    __tablename__ = "quizzes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    #: Set when the teacher puts the quiz away at the end of a course. Nothing
    #: is deleted: an archived quiz keeps its questions, lectures and answers,
    #: and is only left out of the dashboard list and of the term reports, so
    #: a new course starts from a clean slate without last year's lectures in
    #: its gradebook denominator.
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped[User] = relationship()
    questions: Mapped[list["Question"]] = relationship(
        back_populates="quiz", order_by="Question.position", cascade="all, delete-orphan"
    )


class Kind(str, enum.Enum):
    """What a question asks for.

    `choice` is peer instruction: pick one of several, asked twice with a
    discussion in between. `text` is a word cloud — a word or short phrase,
    asked once, with no right answer. The two differ in enough places that the
    kind is stored rather than inferred from whether choices exist: a question
    being edited passes through a moment with no choices, and guessing from
    that would change what it is.
    """

    choice = "choice"
    text = "text"


#: The longest single entry in a word cloud. A cloud is unreadable past a few
#: words, and a phone keyboard mid-lecture argues for the same.
MAX_TEXT_ANSWER = 40

#: How many entries a word-cloud question may ask each student for ("three
#: words that describe systems biology"). Five is already more than a phone
#: screen shows comfortably, and more than a cloud needs from each person.
MAX_WORDS = 5

#: A student's entries are kept in one answer row, one per line, so that a
#: word cloud stays one answer per student per round like every other
#: question: the attendance files, the live count and "last write wins" all
#: rest on that, and none of them has to learn that a cloud is different. A
#: line break cannot be typed into the single-line fields the phone offers,
#: and `normalise_word` removes any that arrive another way.
WORD_SEPARATOR = "\n"
MAX_ANSWER_TEXT = MAX_WORDS * (MAX_TEXT_ANSWER + len(WORD_SEPARATOR))


def split_words(text: str | None) -> list[str]:
    """The entries of one stored word-cloud answer."""
    return [w for w in (text or "").split(WORD_SEPARATOR) if w.strip()]


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    quiz_id: Mapped[int] = mapped_column(ForeignKey("quizzes.id"))
    position: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text)  # markdown allowed
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    kind: Mapped[Kind] = mapped_column(
        Enum(Kind), nullable=False, default=Kind.choice, server_default=Kind.choice.value
    )
    #: For a word cloud, how many entries each student may write. Ignored for
    #: multiple choice, where it stays at its default.
    max_words: Mapped[int] = mapped_column(nullable=False, default=1, server_default="1")

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

    #: A rehearsal against this deployment, not a lecture that happened.
    #:
    #: It exists because the attendance reports walk *every* session their
    #: owner has run, and a session that ran rounds is a lecture to them — so
    #: a load test against the live app would enter the Canvas gradebook
    #: denominator and mark the whole real class absent from a lecture nobody
    #: attended. `service.sessions_in_range` filters these out, which is the
    #: single place that has to hold for every report at once.
    is_loadtest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

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
        CheckConstraint(
            "(choice_id IS NULL) <> (text IS NULL)",
            name="ck_answer_is_choice_or_text",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    round_id: Mapped[int] = mapped_column(ForeignKey("rounds.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    #: Null for a word-cloud answer, which carries `text` instead. Exactly one
    #: of the two is set, and the constraint below is what keeps that true of
    #: rows written by anything other than this application.
    choice_id: Mapped[int | None] = mapped_column(ForeignKey("choices.id"), nullable=True)
    #: A word-cloud answer: up to `MAX_WORDS` entries, one per line.
    text: Mapped[str | None] = mapped_column(String(MAX_ANSWER_TEXT), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    round: Mapped[Round] = relationship(back_populates="answers")
    user: Mapped[User] = relationship()
    choice: Mapped[Choice | None] = relationship()
