"""The QR-code URL.

Students reach the app only by scanning this, so it must point at the hostname
they actually use. On a platform without env vars it is derived from the
request; an explicit PUBLIC_BASE_URL always wins.
"""

from app.config import Settings
from app.public_base import public_base_url
from tests.conftest import make_quiz_with_question


class _FakeRequest:
    def __init__(self, headers):
        self.headers = headers

    class _Url:
        scheme = "http"
        netloc = "testserver"

    url = _Url()


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_derived_from_forwarded_headers():
    req = _FakeRequest({"x-forwarded-proto": "https", "host": "quiz.serve.scilifelab.se"})
    assert public_base_url(req, _settings()) == "https://quiz.serve.scilifelab.se"


def test_explicit_setting_wins_over_headers():
    req = _FakeRequest({"x-forwarded-proto": "https", "host": "internal.cluster.local"})
    s = _settings(public_base_url="https://quiz.example.org/")
    assert public_base_url(req, s) == "https://quiz.example.org"


def test_forwarded_host_preferred_and_proxy_chains_handled():
    req = _FakeRequest(
        {
            "x-forwarded-proto": "https, http",
            "x-forwarded-host": "quiz.serve.scilifelab.se, internal",
            "host": "internal",
        }
    )
    assert public_base_url(req, _settings()) == "https://quiz.serve.scilifelab.se"


def test_join_url_endpoint_uses_the_request_host(teacher_client):
    quiz_id, _, _ = make_quiz_with_question(teacher_client)
    code = teacher_client.post(f"/api/sessions?quiz_id={quiz_id}").json()["code"]
    resp = teacher_client.get(
        f"/api/sessions/{code}/join-url",
        headers={"host": "quiz.serve.scilifelab.se", "x-forwarded-proto": "https"},
    )
    assert resp.json()["url"] == f"https://quiz.serve.scilifelab.se/s/{code}"
