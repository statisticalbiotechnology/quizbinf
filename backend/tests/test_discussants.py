"""Drawing two students to say how they reasoned.

This is the only place in the app where individual names are meant to reach
a projected screen, so what it does and does not disclose is worth pinning.
"""

from tests.conftest import login, make_quiz_with_question


def _session(teacher) -> tuple[int, int, list[int], str]:
    quiz_id, question_id, choice_ids = make_quiz_with_question(teacher)
    code = teacher.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    return quiz_id, question_id, choice_ids, code


def _run_pre(teacher, code, question_id, answers):
    """Open a pre round, let `answers` (client, choice) reply, then halt."""
    round_ = teacher.post(
        f"/api/sessions/{code}/rounds", json={"question_id": question_id, "phase": "pre"}
    ).json()
    for client, choice_id in answers:
        client.post(f"/api/sessions/{code}/answers", json={"choice_id": choice_id})
    teacher.post(f"/api/sessions/{code}/rounds/{round_['id']}/close")


def test_two_students_are_drawn_from_those_who_answered(teacher_client, make_client):
    _, question_id, choices, code = _session(teacher_client)

    answered, silent = [], []
    for name in ("anna", "bo", "cecilia", "david"):
        c = make_client()
        login(c, name)
        answered.append((c, name))
    for name in ("eva", "frida"):
        c = make_client()
        login(c, name)
        c.get(f"/api/sessions/{code}/state")  # joins but never answers
        silent.append(name)

    _run_pre(teacher_client, code, question_id, [(c, choices[0]) for c, _ in answered])

    drawn = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()["names"]

    assert len(drawn) == 2
    assert len(set(drawn)) == 2, "the same student must not be drawn twice"
    # Nobody is asked to defend a position they never took.
    for name in drawn:
        assert name not in silent
        assert name in [n for _, n in answered]


def test_the_draw_says_nothing_about_what_they_answered(teacher_client, make_client):
    """Names go under the whole distribution, so the payload must not carry
    the choice — otherwise projecting it would expose an individual answer."""
    _, question_id, choices, code = _session(teacher_client)
    student = make_client()
    login(student, "anna")
    _run_pre(teacher_client, code, question_id, [(student, choices[1])])

    body = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()

    assert set(body) == {"names", "reel"}
    assert str(choices[1]) not in teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).text


# --- the reel the projected view spins before the names settle -------------


def test_the_reel_is_who_joined_not_who_answered(teacher_client, make_client):
    """The reel is projected, so every name in it is read out to the room.
    Taking it from the joined set means a name flashing past says only that
    the student is in the lecture. Taking it from the answerers would publish
    who answered and, by omission, who did not."""
    _, question_id, choices, code = _session(teacher_client)

    answering = make_client()
    login(answering, "anna")
    watching = make_client()
    login(watching, "bo")
    watching.get(f"/api/sessions/{code}/state")  # joins, never answers

    _run_pre(teacher_client, code, question_id, [(answering, choices[0])])

    body = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()

    assert sorted(body["reel"]) == ["anna", "bo"]
    # …while the draw itself still comes only from those who answered.
    assert body["names"] == ["anna"]


def test_the_reel_can_always_come_to_rest_on_a_drawn_name(teacher_client, make_client):
    """A superset by construction — otherwise the spin could not land."""
    _, question_id, choices, code = _session(teacher_client)
    clients = []
    for name in ("a1", "b2", "c3", "d4"):
        c = make_client()
        login(c, name)
        clients.append(c)
    _run_pre(teacher_client, code, question_id, [(c, choices[0]) for c in clients])

    body = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()
    assert set(body["names"]) <= set(body["reel"])


def test_the_teacher_is_not_on_the_reel_either(teacher_client, make_client):
    _, question_id, choices, code = _session(teacher_client)
    student = make_client()
    login(student, "anna")
    teacher_client.get(f"/api/sessions/{code}/state")  # the teacher's own view
    _run_pre(teacher_client, code, question_id, [(student, choices[0])])

    body = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()
    assert body["reel"] == ["anna"]


def test_the_reel_is_capped_for_a_full_lecture(teacher_client, make_client):
    """A 150-student course must not ship a name list on every draw."""
    from app import service

    _, question_id, choices, code = _session(teacher_client)
    for i in range(service.REEL_LIMIT + 5):
        c = make_client()
        login(c, f"student{i:03d}")
        c.get(f"/api/sessions/{code}/state")

    body = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()
    assert len(body["reel"]) == service.REEL_LIMIT


def test_drawing_again_can_give_a_different_pair(teacher_client, make_client):
    """It is a fresh draw each time, so a teacher can redraw when someone is
    absent rather than being stuck with the first pair."""
    _, question_id, choices, code = _session(teacher_client)
    clients = []
    for name in ("a1", "b2", "c3", "d4", "e5", "f6", "g7", "h8"):
        c = make_client()
        login(c, name)
        clients.append(c)
    _run_pre(teacher_client, code, question_id, [(c, choices[0]) for c in clients])

    seen = set()
    for _ in range(25):
        drawn = teacher_client.get(
            f"/api/sessions/{code}/questions/{question_id}/discussants"
        ).json()["names"]
        seen.add(tuple(sorted(drawn)))

    assert len(seen) > 1, "25 draws from 8 students returned the same pair every time"


def test_a_small_class_returns_everyone_who_answered(teacher_client, make_client):
    _, question_id, choices, code = _session(teacher_client)
    only = make_client()
    login(only, "anna")
    _run_pre(teacher_client, code, question_id, [(only, choices[0])])

    drawn = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()["names"]
    assert drawn == ["anna"]


def test_nobody_answering_draws_nobody(teacher_client):
    _, question_id, _, code = _session(teacher_client)

    drawn = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()["names"]
    assert drawn == []


def test_the_teacher_is_never_drawn(teacher_client, make_client):
    """A teacher who answered while testing the student view should not be
    called on to explain their reasoning to the class."""
    _, question_id, choices, code = _session(teacher_client)
    student = make_client()
    login(student, "anna")
    _run_pre(
        teacher_client, code, question_id, [(student, choices[0]), (teacher_client, choices[0])]
    )

    drawn = teacher_client.get(
        f"/api/sessions/{code}/questions/{question_id}/discussants"
    ).json()["names"]
    assert drawn == ["anna"]


def test_drawing_is_teacher_only(teacher_client, student_client, make_client):
    _, question_id, _, code = _session(teacher_client)
    url = f"/api/sessions/{code}/questions/{question_id}/discussants"

    assert student_client.get(url).status_code == 403
    assert make_client().get(url).status_code == 401


def test_another_teacher_cannot_draw_from_your_session(teacher_client, make_client, monkeypatch):
    from app.config import get_settings

    _, question_id, _, code = _session(teacher_client)
    monkeypatch.setattr(get_settings(), "teacher_usernames", "teach,otherteacher")
    intruder = make_client()
    login(intruder, "otherteacher")

    resp = intruder.get(f"/api/sessions/{code}/questions/{question_id}/discussants")
    assert resp.status_code == 403
