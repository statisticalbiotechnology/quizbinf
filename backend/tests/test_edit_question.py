"""Editing a question after it exists, and after it has been asked.

The constraint that shapes all of this: an answer points at a choice id, so a
choice students have already picked cannot be taken away without stranding
what they answered.
"""

from tests.conftest import login, make_quiz_with_question


def _question(teacher, quiz_id: int) -> dict:
    return teacher.get(f"/api/quizzes/{quiz_id}").json()["questions"][0]


def _ask_and_answer(teacher, student, quiz_id, question_id, choice_id) -> str:
    code = teacher.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    round_ = teacher.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    ).json()
    assert (
        student.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_id}).status_code
        == 200
    )
    teacher.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")
    return code


def test_text_and_choices_can_be_reworded(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": "What does **BLAST** actually do?",
            "choices": [
                {"id": c["id"], "text": c["text"] + " (reworded)", "is_correct": c["is_correct"]}
                for c in q["choices"]
            ],
        },
    )
    assert resp.status_code == 200
    edited = resp.json()
    assert edited["text"] == "What does **BLAST** actually do?"
    # Markdown is re-rendered for the students' copy.
    assert "<strong>BLAST</strong>" in edited["text_html"]
    # Same choices, same ids — so any answers still point somewhere real.
    assert [c["id"] for c in edited["choices"]] == [c["id"] for c in q["choices"]]
    assert all(c["text"].endswith("(reworded)") for c in edited["choices"])


def test_the_correct_choice_can_be_moved(teacher_client):
    """Marking the wrong choice is exactly the mistake worth fixing."""
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)
    assert q["choices"][0]["is_correct"]

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": q["text"],
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": i == 1}
                for i, c in enumerate(q["choices"])
            ],
        },
    )
    assert resp.status_code == 200
    marks = [c["is_correct"] for c in resp.json()["choices"]]
    assert marks == [False, True, False]


def test_choices_can_be_added_and_reordered(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)
    reversed_choices = list(reversed(q["choices"]))

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": q["text"],
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]}
                for c in reversed_choices
            ]
            + [{"text": "A brand new option", "is_correct": False}],
        },
    )
    assert resp.status_code == 200
    out = resp.json()
    assert [c["text"] for c in out["choices"]][:3] == [c["text"] for c in reversed_choices]
    assert out["choices"][-1]["text"] == "A brand new option"
    assert [c["position"] for c in out["choices"]] == [0, 1, 2, 3]


def test_an_unanswered_choice_can_be_removed(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": q["text"],
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]}
                for c in q["choices"][:2]
            ],
        },
    )
    assert resp.status_code == 200
    assert len(resp.json()["choices"]) == 2


def test_a_choice_students_have_picked_cannot_be_removed(teacher_client, student_client):
    """A wrong answer is the interesting case: the distractor a student fell
    for is exactly what a teacher might want to tidy away afterwards, and it
    is precisely what the pre/post comparison is about."""
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    distractor = choice_ids[1]
    code = _ask_and_answer(teacher_client, student_client, quiz_id, question_id, distractor)
    q = _question(teacher_client, quiz_id)

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": q["text"],
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]}
                for c in q["choices"]
                if c["id"] != distractor
            ],
        },
    )
    assert resp.status_code == 409
    assert "already been chosen" in resp.json()["detail"]

    # Nothing was applied, and the recorded answer is still readable.
    comparison = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/comparison"
    ).json()
    assert comparison["pre"][str(distractor)] == 1
    assert len(_question(teacher_client, quiz_id)["choices"]) == 3


def test_an_asked_question_can_still_be_reworded(teacher_client, student_client):
    """Editing is not blocked by having been asked — only removing an
    answered choice is."""
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    _ask_and_answer(teacher_client, student_client, quiz_id, question_id, choice_ids[0])
    q = _question(teacher_client, quiz_id)

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": "Corrected wording",
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]}
                for c in q["choices"]
            ],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "Corrected wording"


def test_exactly_one_correct_choice_is_still_required(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)

    for marks in ([False, False, False], [True, True, False]):
        resp = teacher_client.put(
            f"/api/quizzes/{quiz_id}/questions/{question_id}",
            json={
                "text": q["text"],
                "choices": [
                    {"id": c["id"], "text": c["text"], "is_correct": m}
                    for c, m in zip(q["choices"], marks)
                ],
            },
        )
        assert resp.status_code == 422, marks


def test_a_choice_from_another_question_is_refused(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    other = teacher_client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={
            "text": "Another question",
            "choices": [
                {"text": "a", "is_correct": True},
                {"text": "b", "is_correct": False},
            ],
        },
    ).json()

    resp = teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": "Trying to steal a choice",
            "choices": [
                {"id": other["choices"][0]["id"], "text": "a", "is_correct": True},
                {"text": "b", "is_correct": False},
            ],
        },
    )
    assert resp.status_code == 409


def test_editing_is_teacher_only_and_owner_only(teacher_client, student_client, make_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)
    body = {
        "text": "Hijacked",
        "choices": [
            {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]} for c in q["choices"]
        ],
    }

    assert (
        student_client.put(f"/api/quizzes/{quiz_id}/questions/{question_id}", json=body).status_code
        == 403
    )
    anonymous = make_client()
    assert (
        anonymous.put(f"/api/quizzes/{quiz_id}/questions/{question_id}", json=body).status_code
        == 401
    )
    assert _question(teacher_client, quiz_id)["text"] != "Hijacked"


def test_another_teachers_quiz_cannot_be_edited(teacher_client, make_client, monkeypatch):
    from app.config import get_settings

    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    q = _question(teacher_client, quiz_id)

    # A second teacher, who does not own this quiz.
    settings = get_settings()
    monkeypatch.setattr(settings, "teacher_usernames", "teach,otherteacher")
    intruder = make_client()
    login(intruder, "otherteacher")

    resp = intruder.put(
        f"/api/quizzes/{quiz_id}/questions/{question_id}",
        json={
            "text": "Not yours",
            "choices": [
                {"id": c["id"], "text": c["text"], "is_correct": c["is_correct"]}
                for c in q["choices"]
            ],
        },
    )
    assert resp.status_code == 404
