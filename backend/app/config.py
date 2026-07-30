import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder that must never be used to sign real cookies.
DEV_SECRET = "dev-only-secret"

# SciLifeLab Serve offers no way to set environment variables on an app, so
# settings are also read from a file on the mounted persistent volume. Upload
# `quizbinf.env` into the project storage to configure a Serve deployment.
VOLUME_ENV_FILE = "/home/data/quizbinf.env"


class Settings(BaseSettings):
    """All configuration comes from environment variables (12-factor).

    Sources, in increasing precedence: defaults < `/home/data/quizbinf.env`
    (for platforms without env-var support) < `.env` < real environment.

    Everything has a working default so the app boots on a platform that can
    only give us an image, a port and a mounted directory.
    """

    model_config = SettingsConfigDict(
        env_file=(VOLUME_ENV_FILE, ".env"), extra="ignore"
    )

    # Persistent storage. On Serve this is the project volume's mount path;
    # the database and the generated session secret live here.
    data_dir: str = "/home/data"

    # Unset means "derive from data_dir" (see resolved_database_url).
    database_url: str | None = None
    # Unset means "generate one and persist it" (see resolved_session_secret).
    session_secret: str | None = None
    # Unset means "derive from the incoming request" — see public_base.py.
    # Set it explicitly if the app is reached through a different hostname
    # than the one it sees.
    public_base_url: str | None = None

    # Comma-separated KTH usernames that get the teacher role.
    teacher_usernames: str = ""
    # Mock login must be explicitly enabled and is refused in production.
    mock_login: bool = False
    environment: str = "development"  # "development" | "production"

    # OIDC (login.kth.se) — unused until client registration with KTH IT.
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    @property
    def teachers(self) -> set[str]:
        return {u.strip() for u in self.teacher_usernames.split(",") if u.strip()}

    @property
    def mock_login_allowed(self) -> bool:
        return self.mock_login and self.environment != "production"

    def _writable_data_dir(self) -> Path | None:
        """The data directory, if we can actually write to it."""
        path = Path(self.data_dir)
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write-test"
            probe.touch()
            probe.unlink()
            return path
        except OSError:
            return None

    @property
    def resolved_database_url(self) -> str:
        """Explicit setting, else SQLite on the persistent volume.

        Falling back to the working directory would put the database on the
        container's ephemeral filesystem, losing every answer on redeploy, so
        that is only used when no writable volume exists (e.g. local dev).
        """
        if self.database_url:
            return self.database_url
        data = self._writable_data_dir()
        if data is not None:
            return f"sqlite:///{data / 'quizbinf.db'}"
        return "sqlite:///./quizbinf.db"

    @property
    def resolved_session_secret(self) -> str:
        """Explicit setting, else a random secret persisted on the volume.

        The image is public, so a hardcoded default would let anyone forge a
        session cookie. Generating once and storing it next to the database
        keeps logins valid across restarts without any configuration.
        """
        if self.session_secret and self.session_secret != DEV_SECRET:
            return self.session_secret
        data = self._writable_data_dir()
        if data is not None:
            secret_file = data / "session_secret"
            try:
                if secret_file.exists():
                    stored = secret_file.read_text().strip()
                    if stored:
                        return stored
                generated = secrets.token_urlsafe(48)
                secret_file.write_text(generated)
                secret_file.chmod(0o600)
                return generated
            except OSError:
                pass
        if self.session_secret:
            return self.session_secret
        # No persistence available: random per process. Sessions do not
        # survive a restart, which is safer than a known constant.
        return secrets.token_urlsafe(48)


@lru_cache
def get_settings() -> Settings:
    return Settings()
