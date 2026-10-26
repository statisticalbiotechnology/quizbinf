"""Archiving a quiz at the end of a course, and copying one into the next.

An archive deletes nothing. What it changes is which lectures the term
reports count: a new course's Canvas file must not contain last year's
lectures, or every new student is marked absent from them.
"""

import csv
import io

from tests.conftest import login
from tests.test_semester_report import _run_both_bouts, _session_with_two_questions


def _lecture_with_one_attender(teacher, make_client, username: str) -> tuple[int, str]:
    student = make_client()
    login(student, username)
    quiz_id, code, questions = _session_with_two_questions(teacher)
    question_id, choices = questions[0]
    _run_both_bouts(
        teacher,
        code,
        question_id,
        {"pre": [(student, choices[0])], "post": [(student, choices[0])]},
    )
    return quiz_id, code


def _canvas_rows(teacher, query: str = "") -> list[list[str]]:
    resp = teacher.get(f"/api/reports/canvas-participation.csv{query}")
    assert resp.status_code == 200
    return list(csv.reader(io.StringIO(resp.text)))


def test_an_archived_quiz_leaves_the_term_reports(teacher_client, make_client):
    old_quiz, _ = _lecture_with_one_attender(teacher_client, make_client, "lastyear")
    _lecture_with_one_attender(teacher_client, make_client, "thisyear")

    assert teacher_client.post(f"/api/quizzes/{old_quiz}/archive").status_code == 200

    rows = _canvas_rows(teacher_client)
    assert rows[1][-1] == "1"  # Points Possible: this year's one lecture only
    assert {r[3] for r in rows[2:]} == {"thisyear"}

    report = teacher_client.get("/api/reports/participation").json()
    assert len(report["sessions"]) == 1


def test_archived_lectures_can_still_be_asked_for(teacher_client, make_client):
    old_quiz, _ = _lecture_with_one_attender(teacher_client, make_client, "lastyear")
    teacher_client.post(f"/api/quizzes/{old_quiz}/archive")

    rows = _canvas_rows(teacher_client, "?archived=true")
    assert rows[1][-1] == "1"
    assert {r[3] for r in rows[2:]} == {"lastyear"}


def test_archiving_deletes_nothing_and_can_be_undone(teacher_client, make_client):
    quiz_id, code = _lecture_with_one_attender(teacher_client, make_client, "kept")
    archived = teacher_client.post(f"/api/quizzes/{quiz_id}/archive").json()
    assert archived["archived_at"] is not None
    assert len(archived["questions"]) == 2

    # The lecture itself is still there, answers and all.
    rows = teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"]
    assert [r["username"] for r in rows] == ["kept"]

    restored = teacher_client.post(f"/api/quizzes/{quiz_id}/restore").json()
    assert restored["archived_at"] is None
    assert len(_canvas_rows(teacher_client)) == 3


def test_an_archived_quiz_cannot_start_a_lecture(teacher_client):
    quiz_id, _, _ = _session_with_two_questions(teacher_client)
    teacher_client.post(f"/api/quizzes/{quiz_id}/archive")
    assert teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").status_code == 409


def test_a_copy_has_the_questions_and_none_of_the_lectures(teacher_client, make_client):
    quiz_id, _ = _lecture_with_one_attender(teacher_client, make_client, "old")
    cloud = teacher_client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={"text": "Three words", "kind": "text", "max_words": 3, "choices": []},
    )
    assert cloud.status_code == 201
    teacher_client.post(f"/api/quizzes/{quiz_id}/archive")
    original = teacher_client.get(f"/api/quizzes/{quiz_id}").json()

    resp = teacher_client.post(f"/api/quizzes/{quiz_id}/copy", json={"title": "Next year"})
    assert resp.status_code == 201
    copy = resp.json()
    assert copy["id"] != quiz_id
    assert copy["title"] == "Next year"
    assert copy["archived_at"] is None

    def shape(quiz):
        return [
            (
                q["text"],
                q["kind"],
                q["max_words"],
                [(c["text"], c["is_correct"]) for c in q["choices"]],
            )
            for q in quiz["questions"]
        ]

    assert shape(copy) == shape(original)
    # New rows, so editing the copy cannot rewrite what was asked last year.
    assert {q["id"] for q in copy["questions"]}.isdisjoint(
        q["id"] for q in original["questions"]
    )

    # It runs, and starts with nobody's answers.
    code = teacher_client.post(f"/api/sessions?quiz_id={copy['id']}").json()["code"]
    assert teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"] == []


def test_a_blank_title_keeps_the_original(teacher_client):
    quiz_id, _, _ = _session_with_two_questions(teacher_client)
    copy = teacher_client.post(f"/api/quizzes/{quiz_id}/copy", json={}).json()
    assert copy["title"] == "Term quiz"


def test_only_the_owner_can_archive_or_copy(teacher_client, make_client):
    quiz_id, _, _ = _session_with_two_questions(teacher_client)
    other = make_client()
    login(other, "teach2")
    for action in ("archive", "restore", "copy"):
        resp = other.post(f"/api/quizzes/{quiz_id}/{action}", json={})
        assert resp.status_code in (403, 404), action
