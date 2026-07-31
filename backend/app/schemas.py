from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Phase, Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: str
    display_name: str
    role: Role


class MockLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: str = ""


class ChoiceIn(BaseModel):
    text: str = Field(min_length=1)
    is_correct: bool = False


class QuestionIn(BaseModel):
    text: str = Field(min_length=1)
    image_url: str | None = None
    choices: list[ChoiceIn] = Field(min_length=2)

    @model_validator(mode="after")
    def exactly_one_correct(self) -> "QuestionIn":
        if sum(1 for c in self.choices if c.is_correct) != 1:
            raise ValueError("A question must have exactly one correct choice")
        return self


class QuizIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class ChoiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    text: str


class ChoiceTeacherOut(ChoiceOut):
    is_correct: bool


class QuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    position: int
    text: str
    image_url: str | None
    choices: list[ChoiceOut]


class QuestionTeacherOut(QuestionOut):
    choices: list[ChoiceTeacherOut]


class QuizOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
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
    choice_id: int


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


class HistogramOut(BaseModel):
    round_id: int
    phase: Phase
    counts: dict[int, int]  # choice_id -> number of answers
    total: int


class ComparisonOut(BaseModel):
    question_id: int
    pre: dict[int, int] | None
    post: dict[int, int] | None


class ParticipantsOut(BaseModel):
    """Room size for the projected join screen. Counts only — never names."""

    joined: int
    connected: int


class LiveCountOut(BaseModel):
    """Progress of the open round: how many have answered, not what they chose."""

    open_round: RoundOut | None
    answered: int
