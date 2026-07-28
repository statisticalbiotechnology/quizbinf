"""Tests for the round lifecycle and pre/post pairing — the heart of the app."""

from tests.conftest import make_quiz_with_question


def _session(teacher_client):
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher_client)
    session = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()
    return session["code"], question_id, choice_ids


def test_only_one_round_open_at_a_time(teacher_client):
    code, qid, _ = _session(teacher_client)
    r1 = teacher_client.post(f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"})
    assert r1.status_code == 201
    r2 = teacher_client.post(f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"})
    assert r2.status_code == 409


def test_post_requires_closed_pre(teacher_client):
    code, qid, _ = _session(teacher_client)
    # post before any pre is refused
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).status_code == 409
    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()
    # still refused while pre is open
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).status_code == 409
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre['id']}/close")
    # now post is allowed
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).status_code == 201


def test_no_reopening_same_phase(teacher_client):
    code, qid, _ = _session(teacher_client)
    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre['id']}/close")
    # re-running the pre round for the same question is refused
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).status_code == 409


def test_exactly_one_correct_choice_required(teacher_client):
    quiz = teacher_client.post("/api/quizzes", json={"title": "Q"}).json()
    bad = teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": "two correct?",
            "choices": [
                {"text": "a", "is_correct": True},
                {"text": "b", "is_correct": True},
            ],
        },
    )
    assert bad.status_code == 422
