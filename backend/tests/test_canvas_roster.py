"""Reading the course roster from Canvas.

The roster does two jobs: it says who is supposed to be in the room, and it
maps a Canvas user id to a KTH identity — which is what will let a Canvas
login identify a student without extra API scopes on the developer key.
"""

import httpx
import pytest

from app import canvas, service
from app.config import get_settings
from app.db import SessionLocal
from app.models import RosterEntry, User
from tests.conftest import login


def canvas_user(canvas_id: int, username: str, kthid: str | None = "u1abcdef", **extra) -> dict:
    """A Canvas user record shaped like the real API returns."""
    return {
        "id": canvas_id,
        "name": f"Student {username}",
        "sortable_name": f"{username}, Student",
        "sis_user_id": kthid,
        "login_id": f"{username}@kth.se",
        "email": f"{username}@kth.se",
        **extra,
    }


@pytest.fixture
def mock_canvas(monkeypatch):
    """Serve canned Canvas responses to app.canvas's own httpx client."""

    def install(handler):
        real_client = httpx.Client

        def factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr(canvas.httpx, "Client", factory)

    return install


def test_login_id_becomes_the_kth_username():
    assert canvas.username_from_login_id("shiraza@kth.se") == "shiraza"
    # Another Canvas install might use a bare username.
    assert canvas.username_from_login_id("shiraza") == "shiraza"
    assert canvas.username_from_login_id("MixedCase@kth.se") == "mixedcase"
    assert canvas.username_from_login_id(None) is None
    assert canvas.username_from_login_id("") is None


def test_a_course_larger_than_one_page_is_read_whole(mock_canvas):
    """Canvas paginates and reports no total, so a course bigger than a page
    is silently truncated unless the Link header chain is followed."""
    page_one = [canvas_user(i, f"student{i}") for i in range(100)]
    page_two = [canvas_user(i, f"student{i}") for i in range(100, 137)]

    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(200, json=page_two)
        return httpx.Response(
            200,
            json=page_one,
            headers={
                "Link": '<https://canvas.kth.se/api/v1/courses/1/users?page=2>; rel="next", '
                '<https://canvas.kth.se/api/v1/courses/1/users?page=1>; rel="first"'
            },
        )

    mock_canvas(handler)
    students = canvas.list_course_students("https://canvas.kth.se", "tok", 1)
    assert len(students) == 137


def test_the_token_is_sent_as_a_bearer_header(mock_canvas):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    mock_canvas(handler)
    canvas.list_course_students("https://canvas.kth.se", "sekrit", 1)
    assert seen["auth"] == "Bearer sekrit"


def test_a_user_without_a_login_id_is_skipped_not_guessed(mock_canvas):
    """Storing them under an invented username would put a wrong name in the
    participation record."""
    users = [
        canvas_user(1, "good"),
        {"id": 2, "name": "No login", "sis_user_id": "u1nologin"},
        {"name": "No id at all", "login_id": "ghost@kth.se"},
    ]
    mock_canvas(lambda request: httpx.Response(200, json=users))

    students = canvas.list_course_students("https://canvas.kth.se", "tok", 1)
    assert [s["username"] for s in students] == ["good"]


def test_email_is_not_carried_out_of_canvas(mock_canvas):
    """The app never sends mail, so keeping addresses would be personal data
    stored for no reason."""
    mock_canvas(lambda request: httpx.Response(200, json=[canvas_user(1, "someone")]))

    student = canvas.list_course_students("https://canvas.kth.se", "tok", 1)[0]
    assert set(student) == {"canvas_user_id", "kthid", "username", "display_name"}


@pytest.mark.parametrize(
    "status_code,fragment",
    [(401, "rejected the access token"), (404, "no such course"), (500, "returned 500")],
)
def test_canvas_failures_are_reported_in_the_teachers_terms(
    mock_canvas, status_code, fragment
):
    mock_canvas(lambda request: httpx.Response(status_code, json={}))
    with pytest.raises(canvas.CanvasError) as exc:
        canvas.list_course_students("https://canvas.kth.se", "tok", 1)
    assert fragment in str(exc.value)


def test_an_unreachable_canvas_is_an_error_not_a_crash(mock_canvas):
    def handler(request):
        raise httpx.ConnectError("no route to host")

    mock_canvas(handler)
    with pytest.raises(canvas.CanvasError):
        canvas.list_course_students("https://canvas.kth.se", "tok", 1)


# --- syncing into the database ---------------------------------------------


def _teacher(db) -> User:
    return db.query(User).filter(User.username == "teach").one()


def test_sync_mirrors_canvas_rather_than_accumulating(teacher_client):
    """A student who drops the course disappears on the next sync; that is
    the whole reason to sync instead of uploading a spreadsheet once."""
    db = SessionLocal()
    teacher = _teacher(db)

    first = [
        {"canvas_user_id": 1, "kthid": "u1aaa", "username": "anna", "display_name": "Anna A"},
        {"canvas_user_id": 2, "kthid": "u1bbb", "username": "bo", "display_name": "Bo B"},
    ]
    summary = service.sync_roster(db, teacher, 49207, first)
    assert (summary["added"], summary["removed"], summary["total"]) == (2, 0, 2)

    # Bo drops, Cecilia joins, Anna is renamed.
    second = [
        {"canvas_user_id": 1, "kthid": "u1aaa", "username": "anna", "display_name": "Anna Andersson"},
        {"canvas_user_id": 3, "kthid": "u1ccc", "username": "cecilia", "display_name": "Cecilia C"},
    ]
    summary = service.sync_roster(db, teacher, 49207, second)
    assert (summary["added"], summary["updated"], summary["removed"]) == (1, 1, 1)

    names = {e.username for e in service.course_roster(db, 49207)}
    assert names == {"anna", "cecilia"}
    db.close()


def test_syncing_one_course_leaves_another_alone(teacher_client):
    db = SessionLocal()
    teacher = _teacher(db)
    service.sync_roster(
        db, teacher, 100, [{"canvas_user_id": 1, "kthid": "u1a", "username": "a", "display_name": "A"}]
    )
    service.sync_roster(
        db, teacher, 200, [{"canvas_user_id": 2, "kthid": "u1b", "username": "b", "display_name": "B"}]
    )

    assert [e.username for e in service.course_roster(db, 100)] == ["a"]
    assert [e.username for e in service.course_roster(db, 200)] == ["b"]
    assert {c["course_id"] for c in service.roster_courses(db, teacher)} == {100, 200}
    db.close()


def test_a_canvas_user_without_an_sis_id_is_still_stored(teacher_client):
    """kthid is the identifier to match on, but a Canvas account with no SIS
    record must not be dropped on the floor."""
    db = SessionLocal()
    service.sync_roster(
        db,
        _teacher(db),
        1,
        [{"canvas_user_id": 9, "kthid": None, "username": "guest", "display_name": "Guest"}],
    )
    entry = service.course_roster(db, 1)[0]
    assert entry.kthid is None and entry.username == "guest"
    db.close()


# --- the API ---------------------------------------------------------------


def test_roster_endpoints_are_teacher_only(student_client, make_client):
    assert student_client.get("/api/roster/status").status_code == 403
    assert student_client.get("/api/roster?course_id=1").status_code == 403
    assert student_client.post("/api/roster/sync?course_id=1").status_code == 403
    assert make_client().get("/api/roster/status").status_code == 401


def test_without_a_token_the_teacher_is_told_what_to_set(teacher_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "canvas_token", "")

    status = teacher_client.get("/api/roster/status").json()
    assert status["canvas_configured"] is False

    resp = teacher_client.post("/api/roster/sync?course_id=1")
    assert resp.status_code == 503
    assert "CANVAS_TOKEN" in resp.json()["detail"]


def test_sync_over_the_api_stores_the_roster(teacher_client, monkeypatch, mock_canvas):
    monkeypatch.setattr(get_settings(), "canvas_token", "tok")
    mock_canvas(
        lambda request: httpx.Response(
            200, json=[canvas_user(11, "eva", "u1eva"), canvas_user(12, "frida", "u1frida")]
        )
    )

    summary = teacher_client.post("/api/roster/sync?course_id=49207").json()
    assert summary["total"] == 2 and summary["added"] == 2

    roster = teacher_client.get("/api/roster?course_id=49207").json()
    assert {r["username"] for r in roster} == {"eva", "frida"}
    assert {r["kthid"] for r in roster} == {"u1eva", "u1frida"}
    # The Canvas id is kept: it is what a Canvas login will arrive with.
    assert {r["canvas_user_id"] for r in roster} == {11, 12}


def test_the_canvas_token_is_never_sent_to_a_client(teacher_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "canvas_token", "super-secret-token")

    body = teacher_client.get("/api/roster/status").text
    assert "super-secret-token" not in body
    assert teacher_client.get("/api/roster/status").json()["canvas_configured"] is True


def test_a_canvas_outage_is_a_502_not_a_500(teacher_client, monkeypatch, mock_canvas):
    monkeypatch.setattr(get_settings(), "canvas_token", "tok")
    mock_canvas(lambda request: httpx.Response(401, json={}))

    resp = teacher_client.post("/api/roster/sync?course_id=1")
    assert resp.status_code == 502
    assert "access token" in resp.json()["detail"]
