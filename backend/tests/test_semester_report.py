"""The end-of-term attendance export, across every session a teacher ran.

Deliberately says nothing about correctness: this is the record of who took
part in both bouts, not a mark.
"""

import csv
import io

from tests.conftest import login, make_quiz_with_question


def _session_with_two_questions(teacher) -> tuple[int, str, list[tuple[int, list[int]]]]:
    quiz = teacher.post("/api/quizzes", json={"title": "Term quiz"}).json()
    questions = []
    for text in ("What does BLAST do?", "What is a k-mer?"):
        q = teacher.post(
            f"/api/quizzes/{quiz['id']}/questions",
            json={
                "text": text,
                "choices": [
                    {"text": "right", "is_correct": True},
                    {"text": "wrong", "is_correct": False},
                ],
            },
        ).json()
        questions.append((q["id"], [c["id"] for c in q["choices"]]))
    code = teacher.post(f"/api/sessions?quiz_id={quiz['id']}").json()["code"]
    return quiz["id"], code, questions


def _run_both_bouts(teacher, code, question_id, answerers):
    """Run pre and post for a question; `answerers` maps phase -> clients."""
    for phase in ("pre", "post"):
        round_ = teacher.post(
            f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": phase}
        ).json()
        for client, choice_id in answerers.get(phase, []):
            client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_id})
        teacher.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")


def test_both_bouts_is_what_counts_not_correctness(teacher_client, make_client):
    diligent, partial = make_client(), make_client()
    login(diligent, "adam")
    login(partial, "bo")

    _, code, questions = _session_with_two_questions(teacher_client)
    q1, choices1 = questions[0]

    # adam answers both bouts but picks the *wrong* choice; bo answers only pre.
    _run_both_bouts(
        teacher_client,
        code,
        q1,
        {
            "pre": [(diligent, choices1[1]), (partial, choices1[0])],
            "post": [(diligent, choices1[1])],
        },
    )

    report = teacher_client.get("/api/reports/participation").json()
    by_user = {r["username"]: r for r in report["students"]}

    # Attendance, not marking: a wrong answer in both bouts still counts.
    assert by_user["adam"]["sessions"][0]["took_part"] is True
    assert by_user["bo"]["sessions"][0]["took_part"] is False
    assert by_user["adam"]["attended"] == 1
    assert by_user["bo"]["attended"] == 0
    # Nothing in the payload reveals whether an answer was right.
    assert "correct" not in report["students"][0]["sessions"][0]


def test_every_asked_pair_must_be_answered(teacher_client, make_client):
    student = make_client()
    login(student, "cecilia")

    _, code, questions = _session_with_two_questions(teacher_client)
    (q1, choices1), (q2, choices2) = questions

    _run_both_bouts(teacher_client, code, q1, {"pre": [(student, choices1[0])],
                                               "post": [(student, choices1[0])]})
    # Second question asked in both bouts, student answers neither.
    _run_both_bouts(teacher_client, code, q2, {})

    entry = teacher_client.get("/api/reports/participation").json()["students"][0]["sessions"][0]
    assert entry == {"completed": 1, "asked": 2, "took_part": False}


def test_a_question_asked_only_once_is_not_held_against_anyone(teacher_client, make_client):
    student = make_client()
    login(student, "david")

    quiz_id, question_id, choices = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    round_ = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    ).json()
    student.post(f"/api/sessions/{code}/answers", json={"choice_id": choices[0]})
    teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")

    # Only one bout ever ran: there is nothing to attend, so no verdict.
    entry = teacher_client.get("/api/reports/participation").json()["students"][0]["sessions"][0]
    assert entry["took_part"] is None
    assert entry["asked"] == 0


def test_the_report_spans_sessions_and_filters_by_date(teacher_client, make_client):
    student = make_client()
    login(student, "eva")

    for _ in range(2):
        quiz_id, question_id, choices = make_quiz_with_question(teacher_client)
        code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
        _run_both_bouts(teacher_client, code, question_id,
                        {"pre": [(student, choices[0])], "post": [(student, choices[0])]})

    report = teacher_client.get("/api/reports/participation").json()
    assert len(report["sessions"]) == 2
    assert report["students"][0]["attended"] == 2

    # A window in the past excludes everything run today.
    empty = teacher_client.get("/api/reports/participation?from=2000-01-01&to=2000-12-31").json()
    assert empty["sessions"] == []


def test_the_csv_has_one_column_per_session(teacher_client, make_client):
    student = make_client()
    login(student, "frida")

    _, code, questions = _session_with_two_questions(teacher_client)
    q1, choices1 = questions[0]
    _run_both_bouts(teacher_client, code, q1,
                    {"pre": [(student, choices1[0])], "post": [(student, choices1[0])]})

    resp = teacher_client.get("/api/reports/participation.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(resp.text)))
    assert rows[0][:2] == ["username", "name"]
    assert rows[0][-2:] == ["sessions_attended", "sessions_total"]
    assert code in rows[0][2]
    assert rows[1][0] == "frida"
    assert rows[1][2] == "yes"


def test_partial_attendance_is_visible_in_the_csv(teacher_client, make_client):
    student = make_client()
    login(student, "gustav")

    _, code, questions = _session_with_two_questions(teacher_client)
    (q1, choices1), (q2, _) = questions
    _run_both_bouts(teacher_client, code, q1,
                    {"pre": [(student, choices1[0])], "post": [(student, choices1[0])]})
    _run_both_bouts(teacher_client, code, q2, {})

    rows = list(csv.reader(io.StringIO(teacher_client.get("/api/reports/participation.csv").text)))
    # A strict yes/no would hide that they answered one of the two.
    assert rows[1][2] == "no (1/2)"


def test_a_teacher_sees_only_their_own_sessions(teacher_client, make_client):
    other = make_client()
    login(other, "otherteacher")  # not in TEACHER_USERNAMES, so a student

    quiz_id, question_id, choices = make_quiz_with_question(teacher_client)
    teacher_client.post(f"/api/sessions?quiz_id={quiz_id}")

    assert other.get("/api/reports/participation").status_code == 403
    assert other.get("/api/reports/participation.csv").status_code == 403


def test_the_report_is_not_public(client):
    assert client.get("/api/reports/participation").status_code == 401
    assert client.get("/api/reports/participation.csv").status_code == 401
