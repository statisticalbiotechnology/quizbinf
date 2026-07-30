"""Zero-configuration behaviour.

SciLifeLab Serve gives an app an image, a port and a mounted directory — there
is no way to set environment variables. These tests pin the defaults that make
the app deployable under that constraint.
"""

import os

import pytest

from app.config import DEV_SECRET, Settings


def test_database_lands_on_the_persistent_volume(tmp_path):
    s = Settings(data_dir=str(tmp_path), database_url=None, _env_file=None)
    assert s.resolved_database_url == f"sqlite:///{tmp_path / 'quizbinf.db'}"


def test_explicit_database_url_wins(tmp_path):
    s = Settings(
        data_dir=str(tmp_path),
        database_url="postgresql+psycopg://u:p@db/quizbinf",
        _env_file=None,
    )
    assert s.resolved_database_url == "postgresql+psycopg://u:p@db/quizbinf"


def test_unwritable_data_dir_falls_back_without_crashing():
    s = Settings(data_dir="/proc/nonexistent/nope", database_url=None, _env_file=None)
    assert s.resolved_database_url == "sqlite:///./quizbinf.db"


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permissions")
def test_unwritable_existing_database_falls_back_instead_of_crashing(tmp_path):
    """An earlier root-run container may leave a database this uid cannot write.

    Serve requires non-root containers, so a volume written by a previous
    root image must not take the app down at the migration step.
    """
    db = tmp_path / "quizbinf.db"
    db.touch()
    db.chmod(0o444)
    s = Settings(data_dir=str(tmp_path), database_url=None, _env_file=None)
    assert s.resolved_database_url == "sqlite:///./quizbinf.db"


def test_session_secret_is_generated_and_then_stable(tmp_path):
    first = Settings(data_dir=str(tmp_path), session_secret=None, _env_file=None)
    generated = first.resolved_session_secret
    assert len(generated) > 30
    assert generated != DEV_SECRET
    # A restart must reuse it, or every cookie issued before it is invalidated.
    second = Settings(data_dir=str(tmp_path), session_secret=None, _env_file=None)
    assert second.resolved_session_secret == generated


def test_placeholder_secret_is_never_used(tmp_path):
    s = Settings(data_dir=str(tmp_path), session_secret=DEV_SECRET, _env_file=None)
    assert s.resolved_session_secret != DEV_SECRET


def test_mock_login_refused_in_production(tmp_path):
    s = Settings(
        data_dir=str(tmp_path), mock_login=True, environment="production", _env_file=None
    )
    assert s.mock_login_allowed is False
