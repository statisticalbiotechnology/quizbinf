"""Resetting a question so it can be asked again.

A question can normally be run once per session, which makes rehearsing
awkward. Reset discards its rounds — and the answers they hold — so both
bouts can be run afresh.
"""

from tests.conftest import make_quiz_with_question


def _ran_both_bouts(teacher_client, student_client):
    quiz_id, qid, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]

    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[1]})
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre['id']}/close")

    post = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).json()
    student_client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_ids[0]})
    teacher_client.post(f"/api/sessions/{code}/rounds/{post['id']}/close")
    return code, qid, choice_ids


def test_both_bouts_can_be_run_again_after_a_reset(student_client, teacher_client):
    code, qid, choice_ids = _ran_both_bouts(teacher_client, student_client)

    # Without a reset, the question is spent.
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).status_code == 409

    resp = teacher_client.delete(f"/api/sessions/{code}/questions/{qid}/rounds")
    assert resp.status_code == 200
    assert resp.json()["removed_rounds"] == 2

    # Both phases are available again, in the usual order.
    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    )
    assert pre.status_code == 201
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre.json()['id']}/close")
    assert teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).status_code == 201


def test_reset_discards_the_answers(student_client, teacher_client):
    code, qid, _ = _ran_both_bouts(teacher_client, student_client)
    before = teacher_client.get(f"/api/sessions/{code}/questions/{qid}/comparison").json()
    assert before["pre"] is not None and before["post"] is not None

    teacher_client.delete(f"/api/sessions/{code}/questions/{qid}/rounds")

    after = teacher_client.get(f"/api/sessions/{code}/questions/{qid}/comparison").json()
    assert after["pre"] is None and after["post"] is None

    # Nobody is recorded as having answered it any more. The student only
    # appeared in the report through those answers, so they drop out entirely.
    rows = teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"]
    assert all(row["answered"] == 0 for row in rows)


def test_reset_also_clears_an_open_round(student_client, teacher_client):
    """Rehearsal often means abandoning a round mid-way."""
    quiz_id, qid, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    )
    assert teacher_client.get(f"/api/sessions/{code}/live").json()["open_round"] is not None

    teacher_client.delete(f"/api/sessions/{code}/questions/{qid}/rounds")
    assert teacher_client.get(f"/api/sessions/{code}/live").json()["open_round"] is None


def test_reset_is_teacher_only(student_client, teacher_client):
    code, qid, _ = _ran_both_bouts(teacher_client, student_client)
    assert (
        student_client.delete(f"/api/sessions/{code}/questions/{qid}/rounds").status_code
        == 403
    )


def test_reset_rejects_a_question_from_another_quiz(teacher_client):
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    other_quiz, other_qid, _ = make_quiz_with_question(teacher_client)
    assert (
        teacher_client.delete(
            f"/api/sessions/{code}/questions/{other_qid}/rounds"
        ).status_code
        == 404
    )
