"""Per-student participation — the one personal-data view in the app.

Formative, not grading: the teacher wants to see who is following along. It is
therefore teacher-only, and must never be reachable by a student.
"""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import login, make_quiz_with_question


def _run_one_question(teacher_client, answers):
    """Run pre and post rounds, applying {username: (pre_choice, post_choice)}."""
    quiz_id, qid, choice_ids = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    correct, wrong = choice_ids[0], choice_ids[1]

    clients = {}
    for name in answers:
        c = TestClient(app)
        login(c, name)
        clients[name] = c

    pre = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    ).json()
    for name, (pre_pick, _) in answers.items():
        if pre_pick is not None:
            clients[name].post(
                f"/api/sessions/{code}/answers",
                json={"choice_id": correct if pre_pick else wrong},
            )
    teacher_client.post(f"/api/sessions/{code}/rounds/{pre['id']}/close")

    post = teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "post"}
    ).json()
    for name, (_, post_pick) in answers.items():
        if post_pick is not None:
            clients[name].post(
                f"/api/sessions/{code}/answers",
                json={"choice_id": correct if post_pick else wrong},
            )
    teacher_client.post(f"/api/sessions/{code}/rounds/{post['id']}/close")
    return code


def test_report_shows_who_was_right_before_and_after(teacher_client):
    code = _run_one_question(
        teacher_client,
        {
            "anna": (False, True),  # changed her mind after discussing
            "bo": (True, True),  # right both times
            "cecilia": (False, False),  # wrong both times
        },
    )
    body = teacher_client.get(f"/api/sessions/{code}/participation").json()
    rows = {r["username"]: r for r in body["rows"]}

    assert rows["anna"]["answers"][0] == {
        "question_id": body["questions"][0]["id"],
        "pre": False,
        "post": True,
    }
    assert rows["bo"]["pre_correct"] == 1 and rows["bo"]["post_correct"] == 1
    assert rows["cecilia"]["pre_correct"] == 0 and rows["cecilia"]["post_correct"] == 0
    assert rows["anna"]["answered"] == 2


def test_a_student_who_never_answered_still_appears(teacher_client):
    """Joined but silent is exactly what a teacher wants to notice."""
    quiz_id, qid, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    lurker = TestClient(app)
    login(lurker, "quiet")
    lurker.get(f"/api/sessions/{code}/state")

    rows = teacher_client.get(f"/api/sessions/{code}/participation").json()["rows"]
    quiet = next(r for r in rows if r["username"] == "quiet")
    assert quiet["answered"] == 0
    assert quiet["answers"][0]["pre"] is None
    assert quiet["answers"][0]["post"] is None


def test_report_is_teacher_only(student_client, teacher_client):
    code = _run_one_question(teacher_client, {"anna": (True, True)})
    assert student_client.get(f"/api/sessions/{code}/participation").status_code == 403
    assert student_client.get(f"/api/sessions/{code}/participation.csv").status_code == 403


def test_another_teacher_cannot_read_someone_elses_session(teacher_client, monkeypatch):
    """Teacher role is not enough — the session must be your own."""
    code = _run_one_question(teacher_client, {"anna": (True, True)})

    from app.config import get_settings

    monkeypatch.setenv("TEACHER_USERNAMES", "teach,other_teacher")
    get_settings.cache_clear()
    try:
        colleague = TestClient(app)
        login(colleague, "other_teacher")
        assert colleague.get(f"/api/sessions/{code}/participation").status_code == 403
        assert (
            colleague.get(f"/api/sessions/{code}/participation.csv").status_code == 403
        )
    finally:
        # monkeypatch restores the variable; the cache must be dropped too, or
        # later tests keep the widened allowlist.
        monkeypatch.undo()
        get_settings.cache_clear()


def test_csv_export_has_a_row_per_student(teacher_client):
    code = _run_one_question(
        teacher_client, {"anna": (False, True), "bo": (True, True)}
    )
    resp = teacher_client.get(f"/api/sessions/{code}/participation.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]

    lines = [line for line in resp.text.splitlines() if line.strip()]
    assert lines[0].startswith("username,name,q1_pre,q1_post")
    assert len(lines) == 3  # header + two students
    anna = next(line for line in lines if line.startswith("anna"))
    assert "wrong,correct" in anna
