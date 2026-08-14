"""Recorded answers are the only irreplaceable data in the app.

Everything else — quizzes, questions, sessions — can be typed again. These
tests pin the paths that could quietly lose answers, or lose the ability to
report on them, which amounts to the same thing.
"""

from tests.conftest import make_quiz_with_question


def _run_a_question(teacher, student) -> tuple[int, int, str]:
    """One asked-and-closed pre round with a student's answer recorded."""
    quiz_id, question_id, choices = make_quiz_with_question(teacher)
    code = teacher.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    round_ = teacher.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    ).json()
    assert (
        student.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[0]}).status_code
        == 200
    )
    teacher.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")
    return quiz_id, question_id, code


def test_a_question_with_answers_cannot_be_deleted(teacher_client, student_client):
    """Deleting it does not remove the answers — it strands them.

    Nothing cascades from a question to the rounds that asked it, so the
    answers survive in the database while `Round.question` becomes None, and
    the participation report for the session raises instead of rendering.
    """
    quiz_id, question_id, code = _run_a_question(teacher_client, student_client)

    resp = teacher_client.delete(f"/api/quizzes/{quiz_id}/questions/{question_id}")
    assert resp.status_code == 409
    assert "already been asked" in resp.json()["detail"]

    # And the report still works.
    assert teacher_client.get(f"/api/sessions/{code}/participation").status_code == 200
    assert teacher_client.get(f"/api/sessions/{code}/participation.csv").status_code == 200
    assert (
        teacher_client.get(f"/api/sessions/{code}/questions/{question_id}/comparison").json()["pre"]
        is not None
    )


def test_an_unused_question_can_still_be_deleted(teacher_client):
    quiz_id, question_id, _ = make_quiz_with_question(teacher_client)
    assert teacher_client.delete(f"/api/quizzes/{quiz_id}/questions/{question_id}").status_code == 204


def test_the_report_survives_a_question_deleted_by_an_earlier_build(
    teacher_client, student_client
):
    """Data in the wild may already have stranded rounds; reporting on the
    rest of the session must not be blocked by them."""
    from app.db import SessionLocal
    from app.models import Question

    quiz_id, question_id, code = _run_a_question(teacher_client, student_client)

    # Reproduce the damage the old delete path caused, bypassing the new guard.
    db = SessionLocal()
    db.delete(db.get(Question, question_id))
    db.commit()
    db.close()

    assert teacher_client.get(f"/api/sessions/{code}/participation").status_code == 200
    assert teacher_client.get(f"/api/sessions/{code}/participation.csv").status_code == 200


def test_answers_outlive_the_round_being_closed(teacher_client, student_client):
    """The obvious one, stated so it cannot regress: closing a round ends
    submission, it does not discard what was submitted."""
    _, question_id, code = _run_a_question(teacher_client, student_client)

    comparison = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/comparison"
    ).json()
    assert sum(comparison["pre"].values()) == 1
