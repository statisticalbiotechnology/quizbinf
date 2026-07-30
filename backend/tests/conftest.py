import os
import tempfile

import pytest

# Configure a throwaway SQLite DB and enable mock login before app import.
_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
# Keep the generated session secret out of the real data directory —
# otherwise running the tests writes into /home/data on the host.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="quizbinf-test-data-")
os.environ["MOCK_LOGIN"] = "true"
os.environ["ENVIRONMENT"] = "development"
os.environ["TEACHER_USERNAMES"] = "teach"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def make_client():
    # Each call returns an independent client with its own cookie jar, so a
    # teacher and a student can be logged in side by side.
    return lambda: TestClient(app)


@pytest.fixture
def client():
    return TestClient(app)


def login(client, username: str) -> None:
    resp = client.post("/api/auth/mock-login", json={"username": username})
    assert resp.status_code == 200


@pytest.fixture
def teacher_client():
    c = TestClient(app)
    login(c, "teach")
    return c


@pytest.fixture
def student_client():
    c = TestClient(app)
    login(c, "student1")
    return c


def make_quiz_with_question(client) -> tuple[int, int, list[int]]:
    quiz = client.post("/api/quizzes", json={"title": "Bioinf"}).json()
    q = client.post(
        f"/api/quizzes/{quiz['id']}/questions",
        json={
            "text": "What does BLAST do?",
            "choices": [
                {"text": "Aligns sequences", "is_correct": True},
                {"text": "Folds proteins", "is_correct": False},
                {"text": "Sequences DNA", "is_correct": False},
            ],
        },
    ).json()
    choice_ids = [c["id"] for c in q["choices"]]
    return quiz["id"], q["id"], choice_ids
