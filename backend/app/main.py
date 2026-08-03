import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, status
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
    """Liveness, plus whether anything written here will survive a restart.

    Reported because non-persistent storage does not announce itself: the app
    runs, but the database is thrown away on every restart and the session
    secret is regenerated, which invalidates everyone's cookie. That surfaces
    as unrelated-looking failures — vanished sessions, "invalid session" —
    so it is worth being able to check directly.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "storage": "persistent" if settings._writable_data_dir() else "ephemeral",
    }


def looks_like_asset(full_path: str) -> bool:
    """Whether a path is asking for a build artefact rather than an SPA route.

    Angular's routes never contain a dot ("/s/<code>", "/teacher/session/…"),
    while every emitted artefact has an extension, so the last segment having
    one separates them.
    """
    return "." in full_path.rsplit("/", 1)[-1]


# Angular fingerprints its output ("main-3WBHVWMP.js"). A fingerprinted name
# describes exactly one build, so it can be cached forever; anything else may
# be replaced in place by the next deploy and has to be revalidated.
_FINGERPRINTED = re.compile(r"-[A-Z0-9]{8,}\.[a-z0-9]+$")


def cache_control_for(path: Path) -> str:
    if _FINGERPRINTED.search(path.name):
        return "public, max-age=31536000, immutable"
    return "no-cache"


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
            return FileResponse(asset, headers={"Cache-Control": cache_control_for(asset)})

        # A missing artefact must 404 rather than fall through to the SPA.
        # Answering a ".js" URL with index.html produces "Expected a
        # JavaScript-or-Wasm module script but the server responded with a MIME
        # type of text/html" instead of a plain 404, and the browser may then
        # cache that HTML under the script's URL.
        if looks_like_asset(full_path):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such file")

        # index.html names the fingerprinted bundles of one specific build, so
        # a cached copy outlives the deploy that produced it and asks for chunks
        # that no longer exist. It must be revalidated every time.
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
