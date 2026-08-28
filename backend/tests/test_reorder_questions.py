"""Putting a quiz's questions in the order the lecture will ask them.

Reordering is the one edit that is always safe after a question has been
asked: rounds point at question ids, never at positions.
"""

import pytest


def _quiz_with_questions(client, *texts: str) -> tuple[int, list[int]]:
    quiz_id = client.post("/api/quizzes", json={"title": "Ordering"}).json()["id"]
    ids = []
    for text in texts:
        q = client.post(
            f"/api/quizzes/{quiz_id}/questions",
            json={
                "text": text,
                "image_url": None,
                "choices": [
                    {"text": "a", "is_correct": True},
                    {"text": "b", "is_correct": False},
                ],
            },
        ).json()
        ids.append(q["id"])
    return quiz_id, ids


def _order(client, quiz_id: int) -> list[int]:
    questions = client.get(f"/api/quizzes/{quiz_id}").json()["questions"]
    assert [q["position"] for q in questions] == list(range(len(questions))), (
        "positions must stay a gapless 0..n-1 run"
    )
    return [q["id"] for q in questions]


def test_questions_come_back_in_the_new_order(teacher_client):
    quiz_id, ids = _quiz_with_questions(teacher_client, "first", "second", "third")
    wanted = [ids[2], ids[0], ids[1]]

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/order", json={"question_ids": wanted}
    )

    assert resp.status_code == 200
    assert [q["id"] for q in resp.json()] == wanted
    assert _order(teacher_client, quiz_id) == wanted


def test_reordering_after_a_question_has_been_asked_keeps_its_answers(
    teacher_client, make_client
):
    """The move a teacher makes mid-course. Rounds point at question ids, so
    nothing about a recorded answer depends on the running order."""
    from tests.conftest import login

    quiz_id, ids = _quiz_with_questions(teacher_client, "first", "second")
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    choice = teacher_client.get(f"/api/quizzes/{quiz_id}").json()["questions"][0][
        "choices"
    ][0]["id"]

    round_ = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": ids[0], "phase": "pre"}
    ).json()
    student = make_client()
    login(student, "anna")
    student.post(f"/api/sessions/{code}/answers", json={"choice_id": choice})
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")

    teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/order",
        json={"question_ids": [ids[1], ids[0]]},
    )

    comparison = teacher_client.get(
        f"/api/sessions/{code}/questions/{ids[0]}/comparison"
    ).json()
    assert comparison["pre"][str(choice)] == 1


@pytest.mark.parametrize(
    "bad",
    ["missing", "duplicated", "foreign"],
    ids=["a question left out", "one listed twice", "a question from another quiz"],
)
def test_an_order_that_is_not_the_quizs_questions_is_refused(teacher_client, bad):
    """A partial order would silently renumber from a stale client's view."""
    quiz_id, ids = _quiz_with_questions(teacher_client, "first", "second")
    other_quiz, other_ids = _quiz_with_questions(teacher_client, "elsewhere")

    payload = {
        "missing": [ids[0]],
        "duplicated": [ids[0], ids[0]],
        "foreign": [ids[0], other_ids[0]],
    }[bad]

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/order", json={"question_ids": payload}
    )
    assert resp.status_code == 409
    assert _order(teacher_client, quiz_id) == ids, "a refused order must change nothing"


def test_reordering_is_teacher_only(teacher_client, student_client, make_client):
    quiz_id, ids = _quiz_with_questions(teacher_client, "first", "second")
    body = {"question_ids": list(reversed(ids))}
    url = f"/api/quizzes/{quiz_id}/questions/order"

    assert student_client.put(url, json=body).status_code == 403
    assert make_client().put(url, json=body).status_code == 401


def test_another_teacher_cannot_reorder_your_quiz(teacher_client, make_client, monkeypatch):
    from app.config import get_settings
    from tests.conftest import login

    quiz_id, ids = _quiz_with_questions(teacher_client, "first", "second")
    monkeypatch.setattr(get_settings(), "teacher_usernames", "teach,otherteacher")
    intruder = make_client()
    login(intruder, "otherteacher")

    resp = intruder.put(
        f"/api/quizzes/{quiz_id}/questions/order",
        json={"question_ids": list(reversed(ids))},
    )
    assert resp.status_code == 404
