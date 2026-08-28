"""Exporting a quiz as study material, and attendance for Canvas.

Both leave the app and land somewhere else — a Canvas page, the Canvas
gradebook — so what matters is that they still work once they get there.
"""

import io

from tests.conftest import login


def _quiz(client, title="Alignment lecture") -> int:
    return client.post("/api/quizzes", json={"title": title}).json()["id"]


def _question(client, quiz_id: int, text: str, choices=("right", "wrong")) -> dict:
    return client.post(
        f"/api/quizzes/{quiz_id}/questions",
        json={
            "text": text,
            "image_url": None,
            "choices": [
                {"text": c, "is_correct": i == 0} for i, c in enumerate(choices)
            ],
        },
    ).json()


# --- study material --------------------------------------------------------


def test_markdown_export_carries_the_questions_and_the_answers(teacher_client):
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "Which aligns **locally**?",
              ("Smith-Waterman", "Needleman-Wunsch"))

    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.md").text

    assert "# Alignment lecture" in body
    assert "Which aligns **locally**?" in body
    assert "- [x] Smith-Waterman" in body
    assert "- [ ] Needleman-Wunsch" in body


def test_the_answers_can_be_left_out(teacher_client):
    """For handing the questions out before the class rather than after."""
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "Which?", ("right", "wrong"))

    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.md?answers=false").text
    assert "[x]" not in body
    assert "- [ ] right" in body


def test_a_figure_is_made_absolute_so_it_still_loads_from_canvas(teacher_client):
    """A relative /api/images path resolves against Canvas once pasted there,
    which is to say not at all."""
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "Score it\n\n![](/api/images/abc.png){width=60%}")

    markdown = teacher_client.get(f"/api/quizzes/{quiz_id}/export.md").text
    html = teacher_client.get(f"/api/quizzes/{quiz_id}/export.html").text

    assert "](http://testserver/api/images/abc.png)" in markdown
    assert 'src="http://testserver/api/images/abc.png"' in html
    assert '](/api/images' not in markdown
    assert 'src="/api/images' not in html


def test_an_external_image_is_left_as_the_teacher_wrote_it(teacher_client):
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "![](https://example.org/f.png)")

    assert "https://example.org/f.png" in teacher_client.get(
        f"/api/quizzes/{quiz_id}/export.md"
    ).text


def test_html_export_renders_the_markdown_and_marks_the_answer(teacher_client):
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "Which aligns **locally**?", ("SW", "NW"))

    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.html").text

    assert "<strong>locally</strong>" in body
    assert '<li class="correct">SW' in body
    assert "<li>NW</li>" in body


def test_html_export_cannot_carry_script_out_of_the_app(teacher_client):
    """It goes through the same renderer and sanitiser students see."""
    quiz_id = _quiz(teacher_client)
    _question(teacher_client, quiz_id, "<script>alert(1)</script>",
              ("<img src=x onerror=alert(1)>", "safe"))

    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.html").text
    assert "<script>alert" not in body
    assert "<img src=x" not in body


def test_the_export_follows_the_running_order(teacher_client):
    quiz_id = _quiz(teacher_client)
    first = _question(teacher_client, quiz_id, "asked first")
    second = _question(teacher_client, quiz_id, "asked second")
    teacher_client.put(
        f"/api/quizzes/{quiz_id}/questions/order",
        json={"question_ids": [second["id"], first["id"]]},
    )

    body = teacher_client.get(f"/api/quizzes/{quiz_id}/export.md").text
    assert body.index("asked second") < body.index("asked first")


def test_exports_are_teacher_only(teacher_client, student_client, make_client):
    quiz_id = _quiz(teacher_client)
    for suffix in ("md", "html"):
        url = f"/api/quizzes/{quiz_id}/export.{suffix}"
        assert student_client.get(url).status_code == 403
        assert make_client().get(url).status_code == 401


def test_another_teacher_cannot_export_your_quiz(teacher_client, make_client, monkeypatch):
    from app.config import get_settings

    quiz_id = _quiz(teacher_client)
    monkeypatch.setattr(get_settings(), "teacher_usernames", "teach,otherteacher")
    intruder = make_client()
    login(intruder, "otherteacher")

    assert intruder.get(f"/api/quizzes/{quiz_id}/export.md").status_code == 404


# --- attendance for the Canvas gradebook -----------------------------------


def _run_one_question_session(teacher_client, students) -> None:
    """One session, one question, both bouts, answered by `students`."""
    quiz_id = _quiz(teacher_client, "Lecture")
    question = _question(teacher_client, quiz_id, "Which?")
    choice = question["choices"][0]["id"]
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    for phase in ("pre", "post"):
        round_ = teacher_client.post(
            f"/api/sessions/{code}/rounds",
            json={"question_id": question["id"], "phase": phase},
        ).json()
        for client in students:
            client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice})
        teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")


def _rows(csv_text: str) -> list[list[str]]:
    import csv

    return list(csv.reader(io.StringIO(csv_text)))


def test_canvas_csv_keys_students_on_their_sis_id(teacher_client, make_client, monkeypatch):
    """Canvas matches on an identifier it already holds. The roster is what
    maps our username to that identifier, so the join runs through it."""
    from app import service
    from app.db import SessionLocal
    from app.models import User

    student = make_client()
    login(student, "shiraza")
    _run_one_question_session(teacher_client, [student])

    with SessionLocal() as db:
        teacher = db.query(User).filter(User.username == "teach").one()
        service.sync_roster(
            db,
            teacher,
            63598,
            [{"canvas_user_id": 5, "kthid": "u1abcdef", "username": "shiraza",
              "display_name": "Shiraz Abbas"}],
        )

    body = teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    rows = _rows(body)

    assert rows[0] == [
        "Student", "ID", "SIS User ID", "SIS Login ID", "Quiz participation",
    ]
    # Canvas reads the second row as the denominator, not as a student.
    assert rows[1][0].strip() == "Points Possible"
    assert rows[1][4] == "1"
    # Both identifiers Canvas will match on: its own user id, then the SIS one.
    assert ["Shiraz Abbas", "5", "u1abcdef", "shiraza", "1"] in rows


def test_a_student_missing_from_the_roster_is_still_reported(teacher_client, make_client):
    """Canvas will skip the row, but dropping it here would hide the mismatch
    until the teacher wondered why someone had no mark."""
    student = make_client()
    login(student, "nobody")
    _run_one_question_session(teacher_client, [student])

    rows = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    row = next(r for r in rows if r[3] == "nobody")
    assert row[1] == "", "no Canvas id is known for a student not on the roster"
    assert row[2] == "", "and no SIS id either"


def test_the_mark_is_sessions_attended(teacher_client, make_client):
    attended = make_client()
    login(attended, "present")
    absent = make_client()
    login(absent, "away")

    _run_one_question_session(teacher_client, [attended])
    _run_one_question_session(teacher_client, [attended, absent])

    rows = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    assert rows[1][4] == "2", "two sessions were run"
    assert next(r for r in rows if r[3] == "present")[4] == "2"
    assert next(r for r in rows if r[3] == "away")[4] == "1"


def test_logging_in_is_enough_to_score_the_point(teacher_client, make_client):
    """The Canvas mark is attendance, not completeness: a student who turned
    up and never answered a thing still gets the point for the session."""
    silent = make_client()
    login(silent, "quiet")
    quiz_id = _quiz(teacher_client, "Lecture")
    question = _question(teacher_client, quiz_id, "Which?")
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    silent.get(f"/api/sessions/{code}/state")  # logs in, answers nothing

    for phase in ("pre", "post"):
        round_ = teacher_client.post(
            f"/api/sessions/{code}/rounds",
            json={"question_id": question["id"], "phase": phase},
        ).json()
        teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")

    rows = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    assert next(r for r in rows if r[3] == "quiet")[4] == "1"


def test_answering_only_half_the_questions_still_scores_the_point(
    teacher_client, make_client
):
    """The plain report marks this student down; the gradebook does not. The
    two reports answer different questions on purpose."""
    partial = make_client()
    login(partial, "half")
    quiz_id = _quiz(teacher_client, "Lecture")
    question = _question(teacher_client, quiz_id, "Which?")
    choice = question["choices"][0]["id"]
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]

    for phase in ("pre", "post"):
        round_ = teacher_client.post(
            f"/api/sessions/{code}/rounds",
            json={"question_id": question["id"], "phase": phase},
        ).json()
        if phase == "pre":  # answers the first bout only
            partial.post(f"/api/sessions/{code}/answers", json={"choice_id": choice})
        teacher_client.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")

    canvas = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    assert next(r for r in canvas if r[3] == "half")[4] == "1"

    plain = teacher_client.get("/api/reports/participation").json()
    row = next(r for r in plain["students"] if r["username"] == "half")
    assert row["attended"] == 0, "the plain report still asks for both bouts"


def test_a_student_who_missed_a_lecture_scores_nothing_for_it(teacher_client, make_client):
    regular = make_client()
    login(regular, "regular")
    latecomer = make_client()
    login(latecomer, "latecomer")

    _run_one_question_session(teacher_client, [regular])
    _run_one_question_session(teacher_client, [regular, latecomer])

    rows = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    assert rows[1][4] == "2", "two lectures were run"
    assert next(r for r in rows if r[3] == "regular")[4] == "2"
    assert next(r for r in rows if r[3] == "latecomer")[4] == "1"


def test_the_teacher_is_not_marked_for_their_own_lecture(teacher_client, make_client):
    """They may have answered while testing the student view, and they are
    running the lecture rather than attending it."""
    student = make_client()
    login(student, "anna")
    _run_one_question_session(teacher_client, [student, teacher_client])

    rows = _rows(
        teacher_client.get("/api/reports/canvas-participation.csv?course_id=63598").text
    )
    assert [r[3] for r in rows[2:]] == ["anna"]


def test_the_assignment_column_can_be_named(teacher_client):
    """The column name is what Canvas offers to create an assignment from."""
    body = teacher_client.get(
        "/api/reports/canvas-participation.csv?assignment=Peer%20instruction"
    ).text
    assert _rows(body)[0][4] == "Peer instruction"


def test_the_canvas_csv_is_teacher_only(student_client, make_client):
    url = "/api/reports/canvas-participation.csv"
    assert student_client.get(url).status_code == 403
    assert make_client().get(url).status_code == 401
