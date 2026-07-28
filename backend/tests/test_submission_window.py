"""The teacher-controlled submission window.

Encodes the classroom sequence the app is built around:
show QR -> open for answers -> halt -> open again -> halt -> show statistics.
Outside an open round *no* answer is accepted, which is what stops students
who are not in the room from answering between rounds.
"""

from tests.conftest import make_quiz_with_question


def _prepared_session(teacher_client):
    quiz_id, qid, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    return code, qid, choice_ids


def test_submission_window_opens_and_halts_twice(student_client, teacher_client):
    code, qid, choice_ids = _prepared_session(teacher_client)

    # QR code is up but the teacher has not opened anything yet: no answers.
    assert student_client.post(
        f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]}
    ).status_code == 409

    # 1. Teacher opens the first bout.
    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()
    assert student_client.post(
        f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[1]}
    ).status_code == 200

    # 2. Teacher halts submission — the discussion happens now.
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre['id']}/close")
    assert student_client.post(
        f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]}
    ).status_code == 409

    # 3. Teacher opens the second bout.
    post = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).json()
    assert student_client.post(
        f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]}
    ).status_code == 200

    # 4. Teacher halts again, before showing statistics.
    teacher_client.post(f"/api/sessions/{code}/rounds/{post['id']}/close")
    assert student_client.post(
        f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[1]}
    ).status_code == 409

    # The two distributions survived the halts and are ready to project.
    cmp = teacher_client.get(f"/api/sessions/{code}/questions/{qid}/comparison").json()
    assert cmp["pre"][str(choice_ids[1])] == 1
    assert cmp["post"][str(choice_ids[0])] == 1


def test_student_state_reports_when_submission_is_halted(student_client, teacher_client):
    """A student arriving between rounds is told there is nothing to answer."""
    code, qid, choice_ids = _prepared_session(teacher_client)
    state = student_client.get(f"/api/sessions/{code}/state").json()
    assert state["open_round"] is None
    assert state["question"] is None


def test_live_count_is_teacher_only_and_counts_answers(student_client, teacher_client):
    code, qid, choice_ids = _prepared_session(teacher_client)
    # Nothing open yet.
    assert teacher_client.get(f"/api/sessions/{code}/live").json()["open_round"] is None

    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    )
    live = teacher_client.get(f"/api/sessions/{code}/live").json()
    assert live["answered"] == 0

    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    live = teacher_client.get(f"/api/sessions/{code}/live").json()
    assert live["answered"] == 1
    # Only a count — never the breakdown, which would bias the discussion if
    # the teacher's screen is projected.
    assert "counts" not in live

    # Students cannot see even the count.
    assert student_client.get(f"/api/sessions/{code}/live").status_code == 403
