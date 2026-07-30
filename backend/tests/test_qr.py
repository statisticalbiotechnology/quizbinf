"""The projected QR code.

Scanning this is the only way students reach the app, so it is rendered
server-side rather than in the browser, and it must encode the hostname the
teacher is actually using.
"""

from tests.conftest import make_quiz_with_question


def _session(teacher_client) -> str:
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    return teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]


def test_qr_is_svg_and_matches_the_join_url(teacher_client):
    code = _session(teacher_client)
    headers = {"host": "quiz.serve.scilifelab.se", "x-forwarded-proto": "https"}

    resp = teacher_client.get(f"/api/sessions/{code}/qr.svg", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    body = resp.text
    assert body.lstrip().startswith("<?xml")
    assert "<svg" in body and "<path" in body

    # The image must encode exactly what the join-url endpoint reports.
    join = teacher_client.get(f"/api/sessions/{code}/join-url", headers=headers).json()
    assert join["url"] == f"https://quiz.serve.scilifelab.se/s/{code}"


def test_qr_encodes_exactly_the_join_url(teacher_client):
    """Independently encode the expected URL and compare the rendered image.

    Asserting the response is valid SVG says nothing about what it encodes;
    a code pointing at the wrong host would still look fine on the projector.
    """
    import io

    import qrcode
    import qrcode.constants
    import qrcode.image.svg

    code = _session(teacher_client)
    headers = {"host": "quiz.serve.scilifelab.se", "x-forwarded-proto": "https"}
    served = teacher_client.get(f"/api/sessions/{code}/qr.svg", headers=headers).text

    def encode(url: str) -> str:
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        buf = io.BytesIO()
        qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buf)
        return buf.getvalue().decode()

    assert served == encode(f"https://quiz.serve.scilifelab.se/s/{code}")
    # Guard against the comparison passing vacuously.
    assert served != encode(f"https://wrong.example/s/{code}")


def test_qr_follows_the_host_the_teacher_used(teacher_client):
    """Two different hostnames must produce two different codes."""
    code = _session(teacher_client)
    a = teacher_client.get(
        f"/api/sessions/{code}/qr.svg",
        headers={"host": "quiz.serve.scilifelab.se", "x-forwarded-proto": "https"},
    ).text
    b = teacher_client.get(
        f"/api/sessions/{code}/qr.svg", headers={"host": "localhost:8000"}
    ).text
    assert a != b


def test_qr_is_teacher_only(student_client, teacher_client):
    code = _session(teacher_client)
    assert student_client.get(f"/api/sessions/{code}/qr.svg").status_code == 403


def test_qr_404s_for_an_unknown_session(teacher_client):
    assert teacher_client.get("/api/sessions/nosuch/qr.svg").status_code == 404
