"""Where the cookie-signing secret comes from.

Every request verifies the session cookie against this value, so anything that
lets it differ between two adjacent requests logs everybody out at random: one
call succeeds, the next answers 401 with no state having changed.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=str(tmp_path), session_secret=None)


def test_the_secret_is_generated_once_and_persisted(tmp_path):
    s = _settings(tmp_path)
    first = s.resolved_session_secret

    assert (tmp_path / "session_secret").read_text().strip() == first
    # A second process reading the same volume must agree.
    assert _settings(tmp_path).resolved_session_secret == first


def test_the_secret_does_not_change_between_requests(tmp_path):
    """It is read on every request, so re-reading must not re-generate."""
    s = _settings(tmp_path)
    assert len({s.resolved_session_secret for _ in range(50)}) == 1


def test_a_concurrent_cold_start_agrees_on_one_secret(tmp_path):
    """The case that actually bites: several requests arriving at once with
    no secret file yet — the first burst after a deploy, or after the file has
    been deleted.

    A check-then-write leaves every racer generating its own and overwriting
    the last, so the cookie handed out by one request is rejected by the next.
    """
    settings = [_settings(tmp_path) for _ in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        secrets_seen = set(pool.map(lambda s: s.resolved_session_secret, settings))

    assert len(secrets_seen) == 1, (
        f"{len(secrets_seen)} different secrets from one cold start; "
        "cookies signed by one worker would be rejected by another"
    )
    assert (tmp_path / "session_secret").read_text().strip() in secrets_seen


def test_an_unreadable_secret_file_is_reported_loudly(tmp_path, caplog):
    """Falling back silently is the worst outcome: a per-process random secret
    rejects every cookie the moment there is more than one process, and looks
    exactly like a mysterious 401.
    """
    secret_file = tmp_path / "session_secret"
    secret_file.write_text("stored-secret")
    secret_file.chmod(0o000)

    s = _settings(tmp_path)
    with caplog.at_level(logging.ERROR):
        value = s.resolved_session_secret

    if value == "stored-secret":  # running as root, which ignores the mode
        return
    assert caplog.records, "an unusable session secret was not reported"


def test_an_explicit_secret_wins(tmp_path):
    s = Settings(data_dir=str(tmp_path), session_secret="configured")
    assert s.resolved_session_secret == "configured"
    # And nothing is written to the volume when it was configured.
    assert not (tmp_path / "session_secret").exists()
