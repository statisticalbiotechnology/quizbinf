"""Tests for answer submission rules and aggregate results."""

from tests.conftest import make_quiz_with_question


def _open_session(teacher_client):
    quiz_id, qid, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    rid = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()["id"]
    return code, qid, choice_ids, rid


def test_answer_requires_login(client, teacher_client):
    code, qid, choice_ids, rid = _open_session(teacher_client)
    resp = client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    assert resp.status_code == 401


def test_last_write_wins_while_open(student_client, teacher_client):
    code, qid, choice_ids, rid = _open_session(teacher_client)
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[1]})
    hist = teacher_client.get(f"/api/sessions/{code}/rounds/{rid}/histogram").json()
    # one student, one net answer on their latest choice
    assert hist["total"] == 1
    assert hist["counts"][str(choice_ids[1])] == 1
    assert hist["counts"][str(choice_ids[0])] == 0


def test_no_answer_after_close(student_client, teacher_client):
    code, qid, choice_ids, rid = _open_session(teacher_client)
    teacher_client.post(f"/api/sessions/{code}/rounds/{rid}/close")
    resp = student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    assert resp.status_code == 409


def test_pre_post_comparison(student_client, teacher_client):
    code, qid, choice_ids, pre_id = _open_session(teacher_client)
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[1]})  # wrong
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre_id}/close")
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    )
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})  # correct
    cmp = teacher_client.get(f"/api/sessions/{code}/questions/{qid}/comparison").json()
    assert cmp["pre"][str(choice_ids[1])] == 1
    assert cmp["post"][str(choice_ids[0])] == 1


def test_state_hides_correct_answer(student_client, teacher_client):
    code, qid, choice_ids, rid = _open_session(teacher_client)
    state = student_client.get(f"/api/sessions/{code}/state").json()
    for choice in state["question"]["choices"]:
        assert "is_correct" not in choice
