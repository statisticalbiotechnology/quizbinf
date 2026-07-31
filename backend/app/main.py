import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import FileResponse

from .config import VOLUME_ENV_FILE, get_settings
from .db import Base, engine
from .routers import auth, images, markdown, quizzes, sessions

log = logging.getLogger("quizbinf")

# Built Angular app; present in the production image, absent in development.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def log_startup_summary() -> None:
    """Report what the app actually configured itself with.

    Where there is no way to set environment variables, configuration comes
    from a file on a mounted volume that is awkward to inspect — and a missing
    or stale one shows up only as a confusing 403 at login. Say plainly what
    was found. Never log the session secret or database credentials.
    """
    s = get_settings()
    volume_env = Path(VOLUME_ENV_FILE)
    log.info("config file %s: %s", volume_env, "found" if volume_env.is_file() else "NOT found")

    data = s._writable_data_dir()
    if data is None:
        log.warning(
            "data dir %s is not writable: the database is EPHEMERAL (answers are "
            "lost on restart) and the session secret is per-process (logins drop "
            "on restart). Mount a writable volume there.",
            s.data_dir,
        )
    else:
        log.info("data dir %s is writable", data)

    url = s.resolved_database_url
    # Only the scheme and, for SQLite, the path — a Postgres URL holds a password.
    log.info(
        "database: %s", url if url.startswith("sqlite") else url.split("://", 1)[0] + "://…"
    )

    log.info("environment=%s mock_login=%s", s.environment, s.mock_login)
    if s.mock_login_allowed:
        log.warning(
            "MOCK LOGIN IS ENABLED: anyone who can reach this app may log in as any "
            "username, including a teacher. Do not use with real students."
        )
    else:
        log.info(
            "mock login disabled — login requires the OIDC flow, which is not "
            "implemented yet, so nobody can log in. Set MOCK_LOGIN=true in %s to "
            "allow it (development only).",
            VOLUME_ENV_FILE,
        )
    log.info("teacher usernames configured: %d", len(s.teachers))
    if not s.teachers:
        log.warning("no TEACHER_USERNAMES set: every user will be a student")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic owns the schema in production; create_all covers dev/tests.
    Base.metadata.create_all(bind=engine)
    log_startup_summary()
    yield


app = FastAPI(title="quizbinf", lifespan=lifespan)

settings = get_settings()
if settings.environment != "production":
    # ng serve runs on :4200 during development; cookies need credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4200"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth.router)
app.include_router(images.router)
app.include_router(markdown.router)
app.include_router(quizzes.router)
app.include_router(sessions.router)


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def static_file_for(full_path: str) -> Path | None:
    """The built asset a request refers to, or None to fall back to the SPA.

    Resolves the path and requires the result to stay inside the static
    directory. Without that check a request whose decoded path contains ".."
    escapes it — `STATIC_DIR / "../../home/data/session_secret"` is a real
    file, and serving it would let anyone forge a teacher cookie.
    """
    if not full_path:
        return None
    root = STATIC_DIR.resolve()
    try:
        candidate = (root / full_path).resolve()
    except (OSError, ValueError):
        return None
    if root not in candidate.parents:
        return None
    return candidate if candidate.is_file() else None


if STATIC_DIR.is_dir():

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        # Serve real files (JS/CSS bundles) directly, everything else gets
        # index.html so Angular's router handles /s/<code> etc.
        asset = static_file_for(full_path)
        if asset is not None:
            return FileResponse(asset)
        return FileResponse(STATIC_DIR / "index.html")
