from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from .markdown import render as render_markdown
from .markdown import render_inline
from .models import MAX_TEXT_ANSWER, MAX_WORDS, Kind, Phase, Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    display_name: str
    role: Role


class MockLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = ""


class LoadTestLoginIn(BaseModel):
    """Signing in a throwaway student for a rehearsal against a live app.

    `name` is a label, not an identity: it is prefixed before it becomes a
    username, so nothing here can collide with, or impersonate, a real
    student.
    """

    key: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]+$")


class RosterLoginIn(BaseModel):
    """Identification against the course roster.

    `password` is blank for students — they are identified, not
    authenticated — and is the shared teacher password for a teacher.
    """

    email: str = Field(min_length=1, max_length=200)
    password: str = ""


def _check_choices(question):
    """A multiple-choice question needs choices; a word cloud must have none.

    Both halves matter. Choices on a word-cloud question would be stored and
    never shown, leaving a question whose editor and behaviour disagree; and a
    choice question without exactly one correct answer is the rule the reports
    are built on.
    """
    if question.kind is Kind.text:
        if question.choices:
            raise ValueError("A word-cloud question has no choices")
        return question
    if len(question.choices) < 2:
        raise ValueError("A question must have at least two choices")
    if sum(1 for c in question.choices if c.is_correct) != 1:
        raise ValueError("A question must have exactly one correct choice")
    return question


class ChoiceIn(BaseModel):
    text: str = Field(min_length=1)
    is_correct: bool = False


class QuestionIn(BaseModel):
    text: str = Field(min_length=1)
    image_url: str | None = None
    kind: Kind = Kind.choice
    #: For a word cloud: how many entries each student may write.
    max_words: int = Field(default=1, ge=1, le=MAX_WORDS)
    choices: list[ChoiceIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def choices_match_the_kind(self) -> "QuestionIn":
        return _check_choices(self)


class ChoiceEdit(BaseModel):
    """A choice in an edit. `id` identifies one that already exists.

    Sent back so the server can tell "the same choice, reworded" from "a new
    choice" — which matters because answers point at choice ids, and dropping
    one students have answered would strand their answers.
    """

    id: int | None = None
    text: str = Field(min_length=1)
    is_correct: bool = False


class QuestionEdit(BaseModel):
    text: str = Field(min_length=1)
    image_url: str | None = None
    kind: Kind = Kind.choice
    max_words: int = Field(default=1, ge=1, le=MAX_WORDS)
    choices: list[ChoiceEdit] = Field(default_factory=list)

    @model_validator(mode="after")
    def choices_match_the_kind(self) -> "QuestionEdit":
        return _check_choices(self)


class QuestionOrder(BaseModel):
    """The complete running order, as question ids.

    Complete rather than a single move, so the server can reject a stale
    client instead of renumbering from an order that no longer exists.
    """

    question_ids: list[int] = Field(min_length=1)


class QuizIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class QuizCopyIn(BaseModel):
    """The new quiz's title; blank takes the old one."""

    title: str | None = Field(default=None, max_length=200)


class ChoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    text: str

    @computed_field
    @property
    def text_html(self) -> str:
        """The choice rendered from Markdown and sanitised, as question text is:
        a formula or a figure is as likely in an answer as in the question."""
        return render_inline(self.text)


class ChoiceTeacherOut(ChoiceOut):
    is_correct: bool


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    text: str
    image_url: str | None
    kind: Kind
    max_words: int
    choices: list[ChoiceOut]

    @computed_field
    @property
    def text_html(self) -> str:
        """`text` rendered from Markdown and sanitised, ready to display.

        Sent alongside the source so a client can show either; the authoring
        form edits `text`, everything that displays a question uses this.
        """
        return render_markdown(self.text)


class QuestionTeacherOut(QuestionOut):
    choices: list[ChoiceTeacherOut]


class QuizOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    archived_at: datetime | None = None
    questions: list[QuestionTeacherOut]


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    quiz_id: int
    created_at: datetime


class OpenRoundIn(BaseModel):
    question_id: int
    phase: Phase


class AnswerIn(BaseModel):
    """One submission: a chosen choice, or the words for a cloud.

    `words` carries a cloud answer. `text` is the single-word form it
    replaced, still accepted so a phone holding the previous build mid-lecture
    is not refused; it means the same as a one-element `words`.
    """

    choice_id: int | None = None
    text: str | None = Field(default=None, max_length=MAX_TEXT_ANSWER)
    words: list[str] | None = Field(default=None, max_length=MAX_WORDS)

    @model_validator(mode="after")
    def exactly_one(self) -> "AnswerIn":
        sent = [v for v in (self.choice_id, self.text, self.words) if v is not None]
        if len(sent) != 1:
            raise ValueError("Send either choice_id or words")
        if self.text is not None:
            self.words, self.text = [self.text], None
        if self.words is not None:
            if any(len(w) > MAX_TEXT_ANSWER for w in self.words):
                raise ValueError(f"Each entry is at most {MAX_TEXT_ANSWER} characters")
            if not any(w.strip() for w in self.words):
                raise ValueError("Write a word first")
        return self


class RoundOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    question_id: int
    phase: Phase
    opened_at: datetime
    closed_at: datetime | None


class SessionState(BaseModel):
    """What every student client needs to render the current moment.

    The correct answer is never included here — students only see choices.
    """

    code: str
    quiz_id: int
    quiz_title: str
    open_round: RoundOut | None
    question: QuestionOut | None  # the open round's question, choices unmarked
    my_choice_id: int | None = None
    #: What this student wrote, for a word-cloud question. Their own only:
    #: the cloud itself is the teacher's to project, after the round is halted.
    my_text: str | None = None
    #: The same, entry by entry, for a cloud that asks for several words.
    my_words: list[str] = Field(default_factory=list)


class HistogramOut(BaseModel):
    round_id: int
    phase: Phase
    counts: dict[int, int]  # choice_id -> number of answers
    total: int


class WordOut(BaseModel):
    """One entry in a word cloud: what was written, and how often."""

    text: str
    count: int


class WordCloudOut(BaseModel):
    question_id: int
    round_id: int
    words: list[WordOut]
    answers: int


class ComparisonOut(BaseModel):
    question_id: int
    pre: dict[int, int] | None
    post: dict[int, int] | None


class ParticipantsOut(BaseModel):
    """Room size for the projected join screen. Counts only — never names."""

    joined: int
    connected: int


class ParticipantAnswerOut(BaseModel):
    """One student's verdict on one question. None means "did not answer"."""

    question_id: int
    pre: bool | None
    post: bool | None


class ParticipantRowOut(BaseModel):
    """A student's participation. Personal data — teacher-only."""

    username: str
    display_name: str
    answers: list[ParticipantAnswerOut]
    answered: int
    pre_correct: int
    post_correct: int


class ParticipationReportOut(BaseModel):
    questions: list[QuestionTeacherOut]
    rows: list[ParticipantRowOut]


class LiveCountOut(BaseModel):
    """Progress of the open round: how many have answered, not what they chose."""

    open_round: RoundOut | None
    answered: int
