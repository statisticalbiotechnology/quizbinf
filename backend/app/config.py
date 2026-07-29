from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (12-factor).

    A local .env is read for convenience in development; in Kubernetes the
    values come from the environment / Secrets.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./quizbinf.db"
    # Signs the session cookie; must be set to a strong random value in prod.
    session_secret: str = "dev-only-secret"
    # Needed to build QR-code URLs and the OIDC redirect URI.
    public_base_url: str = "http://localhost:4200"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
