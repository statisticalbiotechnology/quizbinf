"""Markdown in questions, and uploaded figures.

Question text is authored by a teacher but displayed to every student in the
room, so rendering must never produce anything executable.
"""

import io

from app.markdown import render
from tests.conftest import make_quiz_with_question


def _add_question(teacher_client, text: str) -> dict:
    quiz = teacher_client.post("/api/quizzes", json={"title": "MD"}).json()
    return teacher_client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": text,
            "image_url": None,
            "choices": [
                {"text": "a", "is_correct": True},
                {"text": "b", "is_correct": False},
            ],
        },
    ).json()


# --- rendering ---


def test_common_formatting_renders():
    html = render("**bold** and *italic* and `BLAST`")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>BLAST</code>" in html


def test_lists_and_tables_render():
    assert "<li>one</li>" in render("- one\n- two")
    assert "<table>" in render("| a | b |\n|---|---|\n| 1 | 2 |")


def test_script_cannot_survive_rendering():
    html = render("<script>alert('x')</script>")
    assert "<script" not in html.lower()


def test_event_handlers_and_javascript_urls_cannot_execute():
    # Raw HTML is escaped rather than passed through, so the text may still
    # contain the word "onerror" — what matters is that no element is created.
    html = render('<img src=x onerror="alert(1)">')
    assert "<img" not in html.lower()
    assert "&lt;img" in html.lower()

    # A javascript: link must not become an anchor.
    link = render("[go](javascript:alert(1))")
    assert "<a " not in link.lower()
    assert "javascript:" not in link.lower().replace("javascript:alert", "")


def test_images_survive_rendering():
    assert '<img src="/api/images/a.png"' in render("![f](/api/images/a.png)")
    assert '<img src="https://example.org/f.png"' in render("![f](https://example.org/f.png)")


def test_empty_text_is_harmless():
    assert render("") == ""


# --- exposed through the API ---


def test_question_is_served_with_rendered_html(teacher_client):
    q = _add_question(teacher_client, "Which is **local**?")
    assert q["text"] == "Which is **local**?"
    assert "<strong>local</strong>" in q["text_html"]


def test_students_receive_the_rendered_question(student_client, teacher_client):
    quiz_id, qid, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    teacher_client.post(
        f"/api/sessions/{code}/rounds", json={"question_id": qid, "phase": "pre"}
    )
    state = student_client.get(f"/api/sessions/{code}/state").json()
    assert "text_html" in state["question"]
    # The correct answer still must not leak with it.
    for choice in state["question"]["choices"]:
        assert "is_correct" not in choice


# --- uploads ---

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def test_upload_returns_markdown_to_paste(teacher_client):
    resp = teacher_client.post(
        "/api/images",
        files={"file": ("fig.png", io.BytesIO(PNG), "image/png")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["url"].startswith("/api/images/")
    assert body["markdown"] == f"![]({body['url']})"

    # And the figure is served back, so a question can display it.
    got = teacher_client.get(body["url"])
    assert got.status_code == 200
    assert got.content == PNG


def test_students_can_load_an_uploaded_figure(student_client, teacher_client):
    url = teacher_client.post(
        "/api/images", files={"file": ("f.png", io.BytesIO(PNG), "image/png")}
    ).json()["url"]
    # Not teacher-only: the figure is part of the question they must read.
    assert student_client.get(url).status_code == 200


def test_only_teachers_can_upload(student_client):
    resp = student_client.post(
        "/api/images", files={"file": ("f.png", io.BytesIO(PNG), "image/png")}
    )
    assert resp.status_code == 403


def test_non_images_are_refused(teacher_client):
    resp = teacher_client.post(
        "/api/images",
        files={"file": ("notes.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert resp.status_code == 415


def test_svg_is_refused_because_it_can_carry_script(teacher_client):
    resp = teacher_client.post(
        "/api/images",
        files={"file": ("x.svg", io.BytesIO(b"<svg onload=alert(1)>"), "image/svg+xml")},
    )
    assert resp.status_code == 415


def test_a_file_lying_about_its_type_is_refused(teacher_client):
    """The content-type header is whatever the client says it is."""
    resp = teacher_client.post(
        "/api/images",
        files={"file": ("evil.png", io.BytesIO(b"<html>not a png"), "image/png")},
    )
    assert resp.status_code == 415


def test_oversized_uploads_are_refused(teacher_client):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)
    resp = teacher_client.post(
        "/api/images", files={"file": ("big.png", io.BytesIO(big), "image/png")}
    )
    assert resp.status_code == 413


def test_unknown_or_traversing_image_names_are_refused(teacher_client):
    assert teacher_client.get("/api/images/nope.png").status_code == 404
    assert teacher_client.get("/api/images/notanimage.txt").status_code == 404


def test_static_fallback_cannot_serve_files_outside_the_build(tmp_path, monkeypatch):
    """A request path with '..' must not escape the static directory.

    Escaping it would expose the data volume — including the session secret,
    which is enough to forge a teacher cookie. Asserted against the resolver
    directly, because HTTP clients normalise '..' out of a URL before sending.
    """
    from app import main

    static = tmp_path / "static"
    static.mkdir()
    (static / "main.js").write_text("console.log(1)")
    secret = tmp_path / "session_secret"
    secret.write_text("super-secret")
    monkeypatch.setattr(main, "STATIC_DIR", static)

    # A genuine asset is still served.
    assert main.static_file_for("main.js") == (static / "main.js").resolve()

    # Anything climbing out is refused, however it is spelled.
    for escape in (
        "../session_secret",
        "../../etc/passwd",
        "./../../session_secret",
        "assets/../../session_secret",
    ):
        assert main.static_file_for(escape) is None, escape

    # And unknown paths fall through to the SPA rather than erroring.
    assert main.static_file_for("s/abc123") is None
    assert main.static_file_for("") is None
