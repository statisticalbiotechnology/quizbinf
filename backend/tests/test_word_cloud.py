"""The word-cloud question: a word instead of a choice, asked once.

The opening lecture asks what students expect of the course and projects the
answers as a cloud. That is a different shape from everything else here — no
right answer, no second bout, no bars — and these are the rules that keep the
difference from leaking into the parts built for peer instruction.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.service import normalise_word
from tests.conftest import login, make_quiz_with_question


def make_cloud_question(client, quiz_id: int, text: str = "What do you expect to learn?") -> int:
    resp = client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={"text": text, "kind": "text", "choices": []},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def open_round(client, code: str, question_id: int, phase: str = "pre"):
    return client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": phase}
    )


@pytest.fixture
def lecture(teacher_client):
    """A quiz with one word-cloud question, and a session running it."""
    quiz = teacher_client.post("/api/quizzes", json={"title": "Intro"}).json()
    question_id = make_cloud_question(teacher_client, quiz["id"])
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz['id']}").json()["code"]
    return quiz["id"], question_id, code


def answer_as(code: str, username: str, **body) -> int:
    student = TestClient(app)
    login(student, username)
    return student.post(f"/api/sessions/{code}/answers", json=body).status_code


# --- authoring -------------------------------------------------------------


def test_a_word_cloud_question_has_no_choices(teacher_client):
    quiz = teacher_client.post("/api/quizzes", json={"title": "Intro"}).json()
    assert make_cloud_question(teacher_client, quiz["id"])

    with_choices = teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": "Expectations?",
            "kind": "text",
            "choices": [{"text": "a", "is_correct": True}, {"text": "b"}],
        },
    )
    assert with_choices.status_code == 422


def test_a_multiple_choice_question_still_needs_its_choices(teacher_client):
    """The kind is opt-in: leaving it out must keep the old rules exactly."""
    quiz = teacher_client.post("/api/quizzes", json={"title": "Intro"}).json()
    resp = teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions", json={"text": "No choices?", "choices": []}
    )
    assert resp.status_code == 422


def test_the_kind_cannot_change_once_the_question_has_been_asked(teacher_client, lecture):
    """Answers already recorded have the shape of the kind that collected
    them: words with no bar to count them in, or choices nothing displays."""
    quiz_id, question_id, code = lecture
    open_round(teacher_client, code, question_id)
    edit = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": "Now a choice question",
            "kind": "choice",
            "choices": [{"text": "a", "is_correct": True}, {"text": "b"}],
        },
    )
    assert edit.status_code == 409


# --- running it ------------------------------------------------------------


def test_a_word_cloud_is_asked_once(teacher_client, lecture):
    """No discussion to compare across: there is no distribution to shift and
    no right answer to move toward."""
    _quiz_id, question_id, code = lecture
    first = open_round(teacher_client, code, question_id)
    assert first.status_code == 201
    teacher_client.post(f"/api/sessions/{code}/rounds/{first.json()['id']}/close")

    second = open_round(teacher_client, code, question_id, "post")
    assert second.status_code == 409
    assert "once" in second.json()["detail"]


def test_a_word_is_recorded_and_a_choice_is_refused(teacher_client, lecture):
    _quiz_id, question_id, code = lecture
    open_round(teacher_client, code, question_id)
    assert answer_as(code, "stu1", text="alignment") == 200
    assert answer_as(code, "stu2", choice_id=1) in (404, 409)


def test_a_student_sees_their_own_word_and_nobody_else_s(teacher_client, lecture):
    """`my_text` mirrors `my_choice_id`: your own answer comes back so the
    page can show it; the cloud belongs to the projected screen."""
    _quiz_id, question_id, code = lecture
    open_round(teacher_client, code, question_id)
    student = TestClient(app)
    login(student, "stu3")
    student.post(f"/api/sessions/{code}/answers", json={"text": "proteins"})
    state = student.get(f"/api/sessions/{code}/state").json()
    assert state["my_text"] == "proteins"
    assert state["question"]["kind"] == "text"
    assert state["question"]["choices"] == []

    other = TestClient(app)
    login(other, "stu4")
    assert other.get(f"/api/sessions/{code}/state").json()["my_text"] is None


def test_answering_again_replaces_the_word(teacher_client, lecture):
    _quiz_id, question_id, code = lecture
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    student = TestClient(app)
    login(student, "stu5")
    student.post(f"/api/sessions/{code}/answers", json={"text": "frist"})
    student.post(f"/api/sessions/{code}/answers", json={"text": "first"})
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    cloud = teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/cloud").json()
    assert cloud["words"] == [{"text": "first", "count": 1}]
    assert cloud["answers"] == 1


# --- the cloud -------------------------------------------------------------


def test_the_cloud_waits_for_the_round_to_be_halted(teacher_client, lecture):
    """The same rule as the histogram: nothing is projected while the window
    is open — it tells the room what to write, and puts every word on the wall
    before the teacher has seen it."""
    _quiz_id, question_id, code = lecture
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    answer_as(code, "stu6", text="genomics")

    too_early = teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/cloud")
    assert too_early.status_code == 409

    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")
    assert teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/cloud"
    ).status_code == 200


def test_words_are_counted_case_insensitively_and_shown_as_written(teacher_client, lecture):
    """A cloud split between "DNA" and "dna" is two small words instead of one
    big one, which is the whole point of a cloud."""
    _quiz_id, question_id, code = lecture
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    for i, word in enumerate(["DNA", "dna", "DNA", "alignment", "Machine learning"]):
        answer_as(code, f"word{i}", text=word)
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    cloud = teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/cloud").json()
    assert cloud["words"][0] == {"text": "DNA", "count": 3}
    assert {"text": "Machine learning", "count": 1} in cloud["words"]
    assert cloud["answers"] == 5


def test_the_cloud_is_the_session_owner_s(teacher_client, lecture, make_client):
    """It is answers from a lecture, so it follows the same rule as every
    other view of them."""
    _quiz_id, question_id, code = lecture
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    other = make_client()
    login(other, "another-teacher")
    assert other.get(
        f"/api/sessions/{code}/questions/{question_id}/cloud"
    ).status_code in (403, 404)


def test_a_multiple_choice_question_has_no_cloud(teacher_client):
    quiz_id, question_id, _choices = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")
    assert teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/cloud"
    ).status_code == 409


# --- tidying words ---------------------------------------------------------


def test_a_word_is_tidied_but_not_rewritten():
    assert normalise_word("  alignment  ") == "alignment"
    assert normalise_word("alignment!") == "alignment"
    assert normalise_word("machine   learning") == "machine learning"
    # Not rewritten: the inside of a phrase is the student's.
    assert normalise_word("Grey's anatomy") == "Grey's anatomy"
    assert normalise_word("sekvens-analys") == "sekvens-analys"
    assert normalise_word("   ") == ""


def test_an_empty_word_is_refused(teacher_client, lecture):
    _quiz_id, question_id, code = lecture
    open_round(teacher_client, code, question_id)
    assert answer_as(code, "stu7", text="   ") == 422
    assert answer_as(code, "stu8", text="...") == 409


# --- participation ---------------------------------------------------------


def test_writing_a_word_is_a_pass_in_the_participation_report(teacher_client, lecture):
    """The cloud measures presence, so any word passes. Scored against a
    correct choice it does not have, every student who answered was shown
    as wrong."""
    _quiz_id, question_id, code = lecture
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    assert answer_as(code, "stu20", text="anything at all") == 200
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    rows = teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"]
    row = next(r for r in rows if r["username"] == "stu20")
    assert row["answers"] == [{"question_id": question_id, "pre": True, "post": None}]
    assert row["answered"] == 1
    # Presence, not knowledge: it is kept out of the correctness totals.
    assert row["pre_correct"] == 0

    csv_text = teacher_client.get(f"/api/sessions/{code}/participation.csv").text
    line = next(line for line in csv_text.splitlines() if line.startswith("stu20,"))
    assert ",passed,-," in line
    assert "wrong" not in line


# --- several words per student --------------------------------------------


def make_three_word_lecture(client) -> tuple[int, str]:
    quiz = client.post("/api/quizzes", json={"title": "Systems biology"}).json()
    resp = client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": "Three words that describe systems biology",
            "kind": "text",
            "max_words": 3,
            "choices": [],
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["max_words"] == 3
    code = client.post(f"/api/sessions?quiz_id={quiz['id']}").json()["code"]
    return resp.json()["id"], code


def test_a_cloud_can_ask_for_several_words_and_counts_each(teacher_client):
    """Each entry is its own word in the cloud, while the student is still
    one answer: the count under the cloud is people, not words."""
    question_id, code = make_three_word_lecture(teacher_client)
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    assert answer_as(code, "sb1", words=["networks", "modelling", "Emergence"]) == 200
    assert answer_as(code, "sb2", words=["Networks", "feedback", ""]) == 200
    assert answer_as(code, "sb3", words=["emergence"]) == 200
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    cloud = teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/cloud").json()
    counts = {w["text"].casefold(): w["count"] for w in cloud["words"]}
    assert counts == {"networks": 2, "emergence": 2, "modelling": 1, "feedback": 1}
    assert cloud["answers"] == 3


def test_more_words_than_asked_for_are_refused(teacher_client):
    question_id, code = make_three_word_lecture(teacher_client)
    open_round(teacher_client, code, question_id)
    assert answer_as(code, "sb4", words=["a", "b", "c", "d"]) == 409


def test_a_repeated_word_counts_once_for_its_writer(teacher_client):
    """Writing "networks" three times is one vote, not a way to enlarge it."""
    question_id, code = make_three_word_lecture(teacher_client)
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    answer_as(code, "sb5", words=["networks", "Networks", "networks!"])
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    cloud = teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/cloud").json()
    assert cloud["words"] == [{"text": "networks", "count": 1}]


def test_a_student_gets_their_own_words_back(teacher_client):
    question_id, code = make_three_word_lecture(teacher_client)
    open_round(teacher_client, code, question_id)
    student = TestClient(app)
    login(student, "sb6")
    student.post(f"/api/sessions/{code}/answers", json={"words": ["omics", "models"]})

    state = student.get(f"/api/sessions/{code}/state").json()
    assert state["my_words"] == ["omics", "models"]
    assert state["my_text"] == "omics, models"


def test_a_single_text_still_answers_a_cloud(teacher_client, lecture):
    """The form a phone on the previous build sends, mid-lecture."""
    _quiz_id, question_id, code = lecture
    open_round(teacher_client, code, question_id)
    assert answer_as(code, "sb7", text="genomics") == 200
    assert answer_as(code, "sb8", words=["one", "two"]) == 409  # this cloud asks for one


def test_several_words_are_still_one_answer_for_attendance(teacher_client):
    question_id, code = make_three_word_lecture(teacher_client)
    round_id = open_round(teacher_client, code, question_id).json()["id"]
    answer_as(code, "sb9", words=["networks", "models", "data"])
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_id}/close")

    rows = teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"]
    row = next(r for r in rows if r["username"] == "sb9")
    assert row["answered"] == 1
    assert row["answers"][0]["pre"] is True


def test_the_word_count_is_bounded(teacher_client):
    quiz = teacher_client.post("/api/quizzes", json={"title": "Bounds"}).json()
    resp = teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={"text": "Too many", "kind": "text", "max_words": 6, "choices": []},
    )
    assert resp.status_code == 422


def test_the_word_count_can_be_changed_by_editing(teacher_client):
    question_id, code = make_three_word_lecture(teacher_client)
    quiz_id = teacher_client.get(f"/api/sessions/{code}/state").json()["quiz_id"]
    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={"text": "Two words", "kind": "text", "max_words": 2, "choices": []},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["max_words"] == 2
