import asyncio
import hashlib
import logging
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse

from .auth import RENEW_FLAG, passwords_match, set_session_cookie
from .config import VOLUME_ENV_FILE, get_settings
from .db import Base, engine, journal_mode, pool_stats
from .diagnostics import dump_threads, storage_report
from .routers import auth, backup, images, markdown, quizzes, reports, roster, sessions

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

    # A truncated hash, never the secret. Two instances printing different
    # values will reject each other's cookies.
    digest = hashlib.sha256(s.resolved_session_secret.encode()).hexdigest()[:8]
    log.info("instance %s signing cookies with secret %s…", INSTANCE_ID, digest)

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
    if s.roster_login_allowed:
        log.warning(
            "ROSTER LOGIN IS ENABLED: students are identified against the synced "
            "roster with no password, so anyone who knows a classmate's KTH "
            "address can answer as them. Intended as a stop-gap until a real "
            "identity provider is available."
        )
    elif s.roster_login:
        log.error(
            "ROSTER_LOGIN is set but ROSTER_TEACHER_PASSWORD is empty, so roster "
            "login is refused: without it nobody could reach the teacher views, "
            "and a blank password would let any student claim to be a teacher."
        )

    log.info(
        "concurrency cap: %s",
        f"{s.request_slots} database requests, {s.request_queue_seconds}s queue"
        if s.request_slots > 0
        else "OFF (REQUEST_SLOTS=0)",
    )
    log.info("teacher usernames configured: %d", len(s.teachers))
    if not s.teachers:
        log.warning("no TEACHER_USERNAMES set: every user will be a student")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Alembic owns the schema in production; create_all covers dev/tests.
    Base.metadata.create_all(bind=engine)
    journal_mode()  # warm the cache while the database is idle, not mid-freeze
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

#: How many *database-backed* requests may be in the app at once. Below the
#: pool they draw from (`db.SQLITE_POOL_SIZE`), so running out of connections
#: is not something that can happen: the queue forms here instead, where
#: waiting is all it does. Roughly the size of the thread pool that runs
#: synchronous endpoints, since that is how much work can actually proceed.
#:
#: Configurable, and `REQUEST_SLOTS=0` turns the cap off. A limit that can
#: only be changed by rebuilding an image is a limit nobody can back out of
#: with a class in the room, and this one has already needed backing out of.
REQUEST_SLOTS = settings.request_slots

#: How long such a request waits for a slot before being turned away. Short:
#: a student who has waited this long has already reloaded the page, so a
#: longer wait buys nothing and costs a slot the whole time. The student view
#: re-sends a shed answer on its own.
QUEUE_SECONDS = settings.request_queue_seconds

_slots = asyncio.Semaphore(REQUEST_SLOTS) if REQUEST_SLOTS > 0 else None
_in_flight = 0  # for the log line only — the semaphore is the actual limit

#: Not more than one thread dump per this many seconds. A wedged app sheds
#: hundreds of requests a minute and each would otherwise ask for a dump.
STALL_DUMP_INTERVAL = 120
_last_dump = 0.0


def _dump_threads_once(reason: str) -> None:
    """Record where the threads are, the first time things stop moving.

    A duration says a request took 728 seconds; only a stack says whether it
    was parked in `os.fsync`, inside SQLite, or in this application — and that
    difference decides whether the next fix belongs in the app at all. Doing
    it automatically matters more than it sounds: the state lasts until
    somebody restarts, and asking a teacher to catch it live and run a command
    is asking for the evidence to be lost.
    """
    global _last_dump
    now = time.monotonic()
    if now - _last_dump < STALL_DUMP_INTERVAL:
        return
    _last_dump = now
    try:
        dump_threads(reason)
    except Exception as e:  # noqa: BLE001 — diagnosing must never be the fault
        log.warning("could not dump threads: %r", e)


def needs_the_database(path: str) -> bool:
    """Whether this request should be counted against `REQUEST_SLOTS`.

    Only API calls that take a database connection, and this is the whole of
    the rule — a cap in front of anything else does harm and no good.

    It was written the other way round first, as "everything except a couple
    of exemptions", and that shut a lecture out of the app completely. One
    student arriving loads the HTML, half a dozen fingerprinted JS chunks and
    a favicon before a single API call: 150 of them arriving together is well
    over a thousand requests, none of which touch the database, all of which
    were queueing for the same forty slots. What got refused was the page
    itself — `/s/<code>`, `/login`, `/chunk-*.js` — so students could not load
    the app at all, reloaded, and doubled the stampede. Serving a file off
    disk needs no connection and must never wait for one.

    Two API paths are excluded as well. The SSE stream is open for the whole
    lecture and deliberately holds no connection while it runs, so counting it
    would spend the entire allowance on idle streams within one class.
    `/api/health` is excluded for the opposite reason: it is the endpoint that
    has to answer *while* everything else is queueing, which is exactly when
    somebody is trying to find out what is wrong.
    """
    if not path.startswith("/api/"):
        return False  # the SPA's own HTML, and every built asset
    return not (path.endswith("/events") or path.startswith("/api/health"))


@app.middleware("http")
async def limit_concurrency(request: Request, call_next):
    """Cap how many database-backed requests are in flight at once.

    A lecture hall is not a steady load: nothing happens for four minutes and
    then 150 phones submit inside the same second. Without a cap, every one of
    those requests is accepted, each takes a database connection, and they
    starve each other — the pool empties, and requests that would have
    succeeded a moment later fail with a 500 instead. The work does not get
    done faster for having been let in; it only fails.

    With a cap the same burst is served at the same rate and simply queues,
    which is the behaviour a small machine should have. A queue still not
    moving after `QUEUE_SECONDS` is refused honestly — 503 with `Retry-After`,
    which says "ask again", where a 500 says "something is broken" and invites
    nobody to retry.
    """
    global _in_flight

    if _slots is None or not needs_the_database(request.url.path):
        return await call_next(request)
    try:
        await asyncio.wait_for(_slots.acquire(), timeout=QUEUE_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        # The real count, not `REQUEST_SLOTS`. The first version logged the
        # constant, so every line read "40 already in flight" whatever was
        # actually happening — and when this cap did take a lecture down, the
        # log it produced could not say what was holding the slots. Report
        # what is measured, and see the slow-request line below for what is
        # holding them.
        log.warning(
            "shed %s: %d/%d database requests in flight after waiting %ss",
            request.url.path,
            _in_flight,
            REQUEST_SLOTS,
            QUEUE_SECONDS,
        )
        _dump_threads_once(
            f"{REQUEST_SLOTS} database requests in flight; {request.url.path} shed"
        )
        return JSONResponse(
            {"detail": "The server is busy. Please try again."},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "1"},
        )
    _in_flight += 1
    started = time.perf_counter()
    try:
        return await call_next(request)
    finally:
        held = time.perf_counter() - started
        _in_flight -= 1
        _slots.release()
        if held > settings.slow_request_seconds:
            # Name the request that is occupying the cap. Without this the
            # only evidence of saturation is the list of requests it refused,
            # which says nothing about the cause.
            log.warning(
                "slow: %s %s held a slot for %.1fs (%d in flight, pool %s)",
                request.method,
                request.url.path,
                held,
                _in_flight,
                pool_stats(),
            )


@app.middleware("http")
async def renew_session_cookie(request: Request, call_next):
    """Slide the session window forward on a request that used the cookie.

    Applied here rather than in the `current_user` dependency because FastAPI
    merges a dependency's response headers only when the endpoint returns data
    to serialise (routing.py: a returned Response is used as-is). An endpoint
    like qr.svg returns a FileResponse, so a cookie set from the dependency
    would be dropped without a trace. Middleware sees the final response
    whatever produced it.
    """
    response = await call_next(request)
    username = getattr(request.state, RENEW_FLAG, None)
    if username:
        set_session_cookie(response, username, get_settings())
    return response


app.include_router(auth.router)
app.include_router(backup.router)
app.include_router(images.router)
app.include_router(markdown.router)
app.include_router(quizzes.router)
app.include_router(reports.router)
app.include_router(roster.router)
app.include_router(sessions.router)


# Identifies this process across requests. Two different values coming back
# from the same URL mean more than one instance is serving it.
INSTANCE_ID = secrets.token_hex(4)


@app.get("/api/health")
async def health() -> dict:
    """Liveness, plus the two things that silently break logins.

    `async def`, and that is the whole point of it. As a plain `def` FastAPI
    ran it in the thread pool, so when forty requests were stuck holding
    threads this endpoint queued behind them: it took 23 seconds or timed out
    entirely on a wedged deployment — the one moment it exists for. Exempting
    it from the concurrency cap was not enough, because the thread pool is a
    second queue behind that one. Nothing here does I/O: the pool figures are
    in-memory counters and the journal mode is read once at startup and
    cached, so this can and must answer from the event loop.

    Neither announces itself. Non-persistent storage means the database is
    thrown away on every restart; a session secret that differs between
    processes means a cookie issued by one is rejected by the next, which
    shows up as an unexplained 401 rather than as anything about secrets.

    `secret` is a truncated hash, never the secret: enough to compare two
    instances, useless for forging a cookie. Repeat the request a few times —
    if `instance` or `secret` changes between calls, requests are being served
    by processes that do not agree, and logins will fail at random.
    """
    settings = get_settings()
    fingerprint = hashlib.sha256(settings.resolved_session_secret.encode()).hexdigest()
    return {
        "status": "ok",
        "storage": "persistent" if settings._writable_data_dir() else "ephemeral",
        "instance": INSTANCE_ID,
        "secret": fingerprint[:8],
        # The freeze this app has already suffered once is invisible from
        # outside: requests stop being answered while this endpoint keeps
        # saying ok, because it needs no database. `checked_out` climbing to
        # `size` and staying there is that failure, visible from a phone.
        "db_pool": pool_stats(),
        "journal_mode": journal_mode(),
    }


@app.get("/api/health/storage", include_in_schema=False)
async def health_storage(key: str = "") -> dict:
    """Time the volume and SQLite's state on it, and dump every thread's stack.

    For the failure the concurrency work could not explain: a single request
    taking 31 seconds with one other in flight, and 59 seconds with none.
    There is no queue in that, so the answer is below the application — and
    the app had no way to say anything about the layer it sits on.

    Unauthenticated by necessity, key-gated by design: checking a login means
    reading the users table, which is the thing suspected of being slow, so an
    endpoint that authenticates cannot report on a database that will not
    answer. It returns timings, file sizes and stack frames — nothing from any
    table, no configuration values.

    Runs in a thread (`run_in_threadpool`) because it deliberately blocks on
    the disk, and blocking the event loop to find out why things are blocked
    would be its own joke.
    """
    settings = get_settings()
    if not settings.diagnostics_allowed:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Diagnostics are off. Set DIAGNOSTICS_KEY in the volume's config file.",
        )
    if not passwords_match(key, settings.diagnostics_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong diagnostics key")

    _dump_threads_once("requested via /api/health/storage")
    return {
        "instance": INSTANCE_ID,
        "db_pool": pool_stats(),
        "storage": await run_in_threadpool(storage_report, settings),
        "note": "thread stacks are in the application log, not in this reply",
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
