"""What a student sees on their phone after a question has finished.

The phone shows the class's distribution, the student's own two answers and
the correct choice, and each of those is something the rest of the app keeps
hidden until the second bout is halted. These tests pin that moment.
"""

from tests.conftest import login, make_quiz_with_question


def _open(teacher, code, question_id, phase):
    return teacher.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": phase}
    ).json()


def _close(teacher, code, round_):
    teacher.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")


def _lecture(teacher, make_client):
    quiz_id, question_id, choices = make_quiz_with_question(teacher)
    code = teacher.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    anna, bo = make_client(), make_client()
    login(anna, "anna")
    login(bo, "bo")
    return code, question_id, choices, anna, bo


def test_nothing_is_shown_before_the_second_bout_is_halted(teacher_client, make_client):
    code, qid, choices, anna, bo = _lecture(teacher_client, make_client)
    results = lambda: anna.get(f"/api/sessions/{code}/my-results").json()  # noqa: E731

    assert results() == []
    pre = _open(teacher_client, code, qid, "pre")
    anna.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[1]})
    assert results() == [], "the first bout is open"
    _close(teacher_client, code, pre)
    assert results() == [], "between the bouts the bars would steer the discussion"
    post = _open(teacher_client, code, qid, "post")
    assert results() == [], "the second bout is open"
    _close(teacher_client, code, post)
    assert len(results()) == 1


def test_own_answers_and_the_class_in_counts(teacher_client, make_client):
    code, qid, choices, anna, bo = _lecture(teacher_client, make_client)
    pre = _open(teacher_client, code, qid, "pre")
    anna.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[1]})
    bo.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[2]})
    _close(teacher_client, code, pre)
    post = _open(teacher_client, code, qid, "post")
    anna.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[0]})
    bo.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[0]})
    _close(teacher_client, code, post)

    [result] = anna.get(f"/api/sessions/{code}/my-results").json()
    assert result["my_pre"] == choices[1]
    assert result["my_post"] == choices[0]
    assert result["pre"] == {str(choices[0]): 0, str(choices[1]): 1, str(choices[2]): 1}
    assert result["post"] == {str(choices[0]): 2, str(choices[1]): 0, str(choices[2]): 0}
    assert [c["is_correct"] for c in result["question"]["choices"]] == [True, False, False]
    # Counts only: nothing in the payload names another student.
    assert "bo" not in str(result)

    [other] = bo.get(f"/api/sessions/{code}/my-results").json()
    assert (other["my_pre"], other["my_post"]) == (choices[2], choices[0])


def test_a_student_who_did_not_answer_still_sees_the_class(teacher_client, make_client):
    code, qid, choices, anna, bo = _lecture(teacher_client, make_client)
    pre = _open(teacher_client, code, qid, "pre")
    bo.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[0]})
    _close(teacher_client, code, pre)
    _close(teacher_client, code, _open(teacher_client, code, qid, "post"))

    [result] = anna.get(f"/api/sessions/{code}/my-results").json()
    assert (result["my_pre"], result["my_post"]) == (None, None)
    assert result["pre"][str(choices[0])] == 1


def test_word_clouds_are_left_out(teacher_client, make_client):
    code, qid, choices, anna, bo = _lecture(teacher_client, make_client)
    quiz_id = teacher_client.get(f"/api/sessions/{code}/state").json()["quiz_id"]
    cloud = teacher_client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={"text": "What do you expect?", "kind": "text", "choices": []},
    ).json()
    round_ = _open(teacher_client, code, cloud["id"], "pre")
    anna.post(f"/api/sessions/{code}/answers", json={"words": ["genomics"]})
    _close(teacher_client, code, round_)
    assert anna.get(f"/api/sessions/{code}/my-results").json() == []
