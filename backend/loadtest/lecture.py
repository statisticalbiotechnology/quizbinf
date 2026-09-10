"""Drive a whole lecture at a running instance and measure what it costs.

Why this exists: the app was slow in front of a real class of ~150, and it was
slow *at a particular moment* — the teacher refreshed the projected view while
students were answering. Unit tests answer one request at a time, and the
Playwright suite runs three browsers, so neither can see a queue form. This
does: it logs in a whole class, holds one SSE stream open per student the way
a phone does, opens a bout, and makes everyone answer at once — then refreshes
the teacher view in the middle of that burst, which is the collision the class
actually hit.

What it reports is *latency percentiles per endpoint* and every error, not a
single throughput number. Absolute figures from a laptop or a CI container are
pessimistic (server and 150 clients share one machine, and the clients are
Python rather than 150 separate phones); the useful signals are relative — one
endpoint far slower than the rest, a p99 an order of magnitude past its p50, a
`database is locked`, or a 500.

    python -m loadtest.lecture --base-url http://localhost:8000

Run it against a *throwaway* instance: it creates a quiz, a session and 150
users, and it answers questions as them. Mock login must be enabled, so it
cannot be pointed at production even by accident.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field

import httpx

#: Sockets one simulated student may hold at once.
#:
#: A browser opens about six connections per origin, so this is what a phone
#: does — and, more to the point, what this process can afford. Each student
#: is a separate client with its own cookie jar, so a per-client limit of 400
#: meant two hundred students could ask the kernel for 80,000 sockets: the
#: run died of `ConnectError: All connection attempts failed` before it had
#: measured anything, which reads like the server refusing connections and is
#: not.
PER_CLIENT_CONNECTIONS = 6

#: Room for the sockets above plus the process's own files, with slack. A
#: student holds one SSE stream for the whole lecture, so the floor is one per
#: student whatever else is happening.
def file_descriptors_needed(students: int) -> int:
    return students * PER_CLIENT_CONNECTIONS + 256


def raise_file_descriptor_limit(students: int) -> str | None:
    """Lift this process's own fd ceiling, or say what to do about it.

    The default soft limit is often 1024, which two hundred students exceed
    during the arrival stampede — and the failure surfaces as a connection
    error that looks like the server's fault. The soft limit can usually be
    raised up to the hard limit without privilege, so do that rather than
    making the operator discover `ulimit -n` from a traceback.
    """
    import resource

    needed = file_descriptors_needed(students)
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft >= needed:
        return None
    target = min(needed, hard)
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError) as e:  # pragma: no cover - platform-dependent
        return f"could not raise the open-file limit from {soft}: {e!r}"
    if target < needed:
        return (
            f"the open-file limit is capped at {hard} by the system, but about "
            f"{needed} are needed for {students} students. Run fewer students, "
            f"or raise the hard limit (`ulimit -Hn`)."
        )
    return None

#: Must match `app.auth.COOKIE_NAME` — this is the cookie a teacher copies out
#: of their browser to lend this run their own (already existing) privilege.
SESSION_COOKIE = "quizbinf_session"


@dataclass
class Sample:
    label: str
    seconds: float
    status: int
    note: str = ""

    @property
    def failed(self) -> bool:
        return self.status >= 400 or self.status == 0


@dataclass
class Recorder:
    samples: list[Sample] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add(self, sample: Sample) -> None:
        self.samples.append(sample)
        if sample.failed:
            self.errors.append(f"{sample.label}: HTTP {sample.status} {sample.note}".strip())

    async def timed(self, label: str, coro_factory) -> httpx.Response | None:
        started = time.perf_counter()
        try:
            response = await coro_factory()
        except Exception as e:  # noqa: BLE001 — a client-side failure is a result
            self.add(Sample(label, time.perf_counter() - started, 0, repr(e)))
            return None
        note = "" if response.status_code < 400 else response.text[:120]
        self.add(Sample(label, time.perf_counter() - started, response.status_code, note))
        return response

    def by_label(self) -> dict[str, list[Sample]]:
        grouped: dict[str, list[Sample]] = defaultdict(list)
        for sample in self.samples:
            grouped[sample.label].append(sample)
        return grouped


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank, so a p99 over 150 samples names an actual request."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(fraction * len(ordered) + 0.5) - 1))
    return ordered[index]


def report(recorder: Recorder) -> None:
    grouped = recorder.by_label()
    width = max((len(label) for label in grouped), default=10)
    print()
    print(f"{'endpoint':<{width}}  {'n':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}  fail")
    print("-" * (width + 50))
    for label in sorted(grouped):
        samples = grouped[label]
        times = [s.seconds for s in samples]
        failures = sum(1 for s in samples if s.failed)
        print(
            f"{label:<{width}}  {len(samples):>5} "
            f"{percentile(times, 0.50):>8.3f} {percentile(times, 0.95):>8.3f} "
            f"{percentile(times, 0.99):>8.3f} {max(times):>8.3f}  {failures or '':>4}"
        )
    if recorder.errors:
        print(f"\n{len(recorder.errors)} failed request(s):")
        seen: dict[str, int] = defaultdict(int)
        for error in recorder.errors:
            seen[error] += 1
        for error, count in sorted(seen.items(), key=lambda kv: -kv[1])[:15]:
            print(f"  {count:>4}x {error}")
    else:
        print("\nno failed requests")


# --- the lecture -----------------------------------------------------------


#: Errors that mean "no socket right now", as opposed to a reply we dislike.
CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError, httpx.RemoteProtocolError)


async def stubborn(what, attempts: int = 6, first_wait: float = 1.0):
    """Retry a request that could not get a connection at all.

    Only for the handful of calls this run cannot do without — the teacher's
    own, and the purge. A student's request is a measurement and must be
    recorded as it happened; retrying one would launder a failure into a
    success and flatter the numbers.

    The teacher's are different. During the arrival burst, two hundred clients
    on one machine can leave nothing spare for a two-hundred-and-first
    connection, and a single `ConnectError` then killed the entire run — after
    which the purge, being one more new connection, failed too and left the
    rehearsal in the database. Waiting a second and asking again costs
    nothing and is what a person would do.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return await what()
        except CONNECT_ERRORS as e:  # noqa: PERF203
            last = e
            if attempt < attempts - 1:
                await asyncio.sleep(first_wait * (2**attempt))
    raise last  # type: ignore[misc]


async def watch_health(client: httpx.AsyncClient, stop: asyncio.Event, seen: list[dict]) -> None:
    """Poll `/api/health` throughout, and remember the worst pool reading.

    `/api/health` touches no database on purpose, so it is the one endpoint
    that keeps answering while every other one is stuck waiting for a
    connection — which is precisely the failure being hunted. If the pool is
    the bottleneck, `checked_out` pinned at `size` says so outright, where the
    latency table only says "slow".
    """
    while not stop.is_set():
        try:
            payload = (await client.get("/api/health", timeout=5)).json()
            seen.append(payload.get("db_pool", {}))
        except Exception:  # noqa: BLE001 — health being unreachable is itself data
            seen.append({"checked_out": None})
        await asyncio.sleep(0.25)


#: `src="…"` / `href="…"` of the built assets an Angular page pulls in.
ASSET_REF = re.compile(r'(?:src|href)="(/[^"]+\.(?:js|css|ico|png|woff2?))"')


async def load_the_page(
    client: httpx.AsyncClient, path: str, recorder: Recorder
) -> None:
    """Fetch a page the way a phone does: the HTML, then what it references.

    Not decoration. A student's first act is to load the app, and that is the
    HTML plus half a dozen fingerprinted chunks and a favicon *before* any API
    call — so a class arriving together is over a thousand requests that touch
    no database at all. Driving only the API missed that completely, and a
    concurrency cap that covered these requests as well shut a real lecture
    out of the app: students were refused the page, reloaded, and doubled the
    stampede. Whatever guards the app has, this is the traffic they meet
    first.
    """
    page = await recorder.timed("page html", lambda: client.get(path))
    if page is None or page.status_code >= 400:
        return
    assets = sorted(set(ASSET_REF.findall(page.text)) | {"/favicon.ico"})
    await asyncio.gather(
        *[
            recorder.timed("page asset", lambda a=asset: client.get(a))
            for asset in assets
        ]
    )


async def login(client: httpx.AsyncClient, username: str) -> httpx.Response:
    return await client.post("/api/auth/mock-login", json={"username": username})


async def sign_in_student(
    client: httpx.AsyncClient, index: int, key: str | None
) -> httpx.Response:
    """Sign in one throwaway student, by whichever door this run may use.

    With a key, `/api/auth/loadtest-login` — which mints students only, under
    a reserved prefix, so a rehearsal against a live deployment cannot collide
    with or impersonate anyone real. Without one, the instance offers mock
    login and there is nobody to impersonate.
    """
    if key:
        return await client.post(
            "/api/auth/loadtest-login", json={"key": key, "name": f"s{index:04d}"}
        )
    return await login(client, f"loadstudent{index:03d}")


#: Reused between runs, and named so it is obvious on the dashboard what it is
#: and that it can be ignored.
QUIZ_TITLE = "Load test (rehearsal — not a real quiz)"


async def make_quiz(client: httpx.AsyncClient, questions: int) -> tuple[int, list[dict]]:
    """Find this run's quiz, or create it.

    Reused rather than made afresh each time because the purge afterwards
    removes the *session*, and nothing in this app deletes a quiz — so a new
    one per run would leave the teacher's dashboard filling up with rehearsals
    that cannot be tidied away.
    """
    existing = (await client.get("/api/quizzes")).json()
    for quiz in existing:
        if quiz["title"] == QUIZ_TITLE and len(quiz.get("questions", [])) == questions:
            return quiz["id"], quiz["questions"]

    quiz = (await client.post("/api/quizzes", json={"title": QUIZ_TITLE})).json()
    made = []
    for i in range(questions):
        made.append(
            (
                await client.post(
                    f"/api/quizzes/{quiz['id']}/questions",
                    json={
                        "text": f"Load-test question {i + 1}",
                        "choices": [
                            {"text": "Smith-Waterman", "is_correct": True},
                            {"text": "Needleman-Wunsch", "is_correct": False},
                            {"text": "BLAST", "is_correct": False},
                            {"text": "HMMER", "is_correct": False},
                        ],
                    },
                )
            ).json()
        )
    return quiz["id"], made


async def hold_stream(
    client: httpx.AsyncClient, code: str, recorder: Recorder, stop: asyncio.Event
) -> None:
    """One student's SSE stream, open for the whole lecture like a phone's.

    Time to *first byte* is what is measured: it is when the server got round
    to this student, and it is the number that grows when the event loop is
    blocked.
    """
    started = time.perf_counter()
    try:
        async with client.stream("GET", f"/api/sessions/{code}/events", timeout=60) as r:
            first = time.perf_counter() - started
            recorder.add(Sample("sse connect", first, r.status_code))
            if r.status_code >= 400:
                return
            async for _ in r.aiter_lines():
                if stop.is_set():
                    return
    except Exception as e:  # noqa: BLE001
        recorder.add(Sample("sse connect", time.perf_counter() - started, 0, repr(e)))


async def teacher_view_refresh(
    client: httpx.AsyncClient, code: str, quiz_id: int, questions: list[dict], recorder: Recorder
) -> None:
    """What pressing F5 on the projected teacher view actually sends.

    The Report view loads the session, the quiz and then one comparison per
    question, and re-opens its own SSE stream — so a refresh is not one request
    but N+3 of them arriving together. That is the "unusual clash" this whole
    harness exists to reproduce.
    """
    started = time.perf_counter()
    await asyncio.gather(
        recorder.timed("teacher join-url", lambda: client.get(f"/api/sessions/{code}/join-url")),
        recorder.timed("teacher state", lambda: client.get(f"/api/sessions/{code}/state")),
        recorder.timed("teacher quiz", lambda: client.get(f"/api/quizzes/{quiz_id}")),
        *[
            recorder.timed(
                "teacher comparison",
                lambda q=q: client.get(
                    f"/api/sessions/{code}/questions/{q['id']}/comparison"
                ),
            )
            for q in questions
        ],
    )
    recorder.add(Sample("TEACHER REFRESH (whole)", time.perf_counter() - started, 200))


async def purge(args: argparse.Namespace, teacher: httpx.AsyncClient, code: str) -> None:
    """Take the rehearsal back out.

    On a throwaway instance this is tidiness; against a live deployment it is
    the point — the session and the students it invented would otherwise be
    permanent residents of a database that holds a real class. The session is
    flagged, so no attendance report counts it even before this, but rows
    nobody wants are still rows.
    """
    if not args.purge:
        print(f"left session {code} in place (--no-purge); DELETE /api/sessions/{code} to remove")
        return
    try:
        # Stubbornly: this is the safety net, and it runs straight after the
        # burst that may have used up every socket the machine had. Giving up
        # on the first refusal is how a rehearsal got left in the production
        # database twice.
        gone = await stubborn(
            lambda: teacher.request("DELETE", f"/api/sessions/{code}", timeout=60),
            attempts=8,
        )
    except Exception as e:  # noqa: BLE001 — say what is left behind, whatever went wrong
        gone = None
        note = repr(e)
    else:
        note = f"HTTP {gone.status_code} {gone.text[:200]}"
    if gone is not None and gone.status_code == 200:
        print(f"purged session {code}: {json.dumps(gone.json())}")
        return
    print(
        f"COULD NOT PURGE session {code}: {note}\n"
        f"Remove it by hand, as the teacher:\n"
        f"  curl -X DELETE -H \"Cookie: {SESSION_COOKIE}=$QUIZBINF_TEACHER_COOKIE\" \\\n"
        f"    {args.base_url.rstrip('/')}/api/sessions/{code}",
        file=sys.stderr,
    )


async def run(args: argparse.Namespace) -> int:
    recorder = Recorder()
    warning = raise_file_descriptor_limit(args.students)
    if warning:
        print(f"warning: {warning}", file=sys.stderr)
    limits = httpx.Limits(
        max_connections=PER_CLIENT_CONNECTIONS,
        max_keepalive_connections=PER_CLIENT_CONNECTIONS,
    )

    def new_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=args.timeout)

    # Establish how this run may sign people in, before creating a single row.
    #
    # This script invents students and answers questions as them. On a
    # throwaway instance that costs nothing; on a deployment with a real class
    # in it, it writes into the database that holds their answers — so the
    # only two ways in are a deployment that offers mock login (which
    # `ENVIRONMENT=production` refuses, so an instance offering it has no real
    # students by construction) or a `--loadtest-key` that somebody set
    # deliberately. Checked against the server rather than trusted from this
    # command line: a flag protects nobody from a mistyped `--base-url`.
    try:
        methods = (await new_client().get("/api/auth/methods")).json()
    except Exception as e:  # noqa: BLE001
        print(f"could not reach {args.base_url}: {e!r}", file=sys.stderr)
        return 2
    if not methods.get("mock_login") and not args.loadtest_key:
        print(
            f"REFUSING to load-test {args.base_url}: it does not offer mock login "
            f"({methods}), so it is a real deployment with real students in it.\n"
            "\n"
            "Two ways forward, in `deploy/LOADTEST.md`:\n"
            "  * point this at a throwaway instance with MOCK_LOGIN=true, or\n"
            "  * set a long LOADTEST_KEY on the deployment and pass it here with\n"
            "    --loadtest-key, which signs in throwaway students only and marks\n"
            "    the session so no attendance report counts it as a lecture.",
            file=sys.stderr,
        )
        return 2

    teacher = new_client()
    if args.teacher_cookie:
        # A real deployment has no scriptable teacher login, and it must not
        # grow one: the teacher views hold every student's participation
        # record, so a key that could mint a teacher would be a way to read
        # the class's personal data. The teacher instead signs in as
        # themselves in a browser and lends this run the resulting cookie —
        # no new privilege, and nothing here can exceed what they already had.
        teacher.cookies.set(SESSION_COOKIE, args.teacher_cookie)
        who = await teacher.get("/api/auth/me")
        if who.status_code != 200 or who.json().get("role") != "teacher":
            print(
                f"the --teacher-cookie is not a teacher session: HTTP "
                f"{who.status_code} {who.text[:200]}\n"
                "Log in to the deployment in a browser and copy the "
                f"`{SESSION_COOKIE}` cookie value.",
                file=sys.stderr,
            )
            return 2
    else:
        response = await login(teacher, args.teacher)
        if response.status_code != 200:
            print(
                f"could not log in as {args.teacher}: HTTP {response.status_code} "
                f"{response.text[:200]}\n"
                "The instance needs MOCK_LOGIN=true and this username in "
                "TEACHER_USERNAMES, or pass --teacher-cookie.",
                file=sys.stderr,
            )
            return 2
        # Signing in is not the same as being a teacher: mock login accepts
        # any username and makes a *student* of one that is not in
        # TEACHER_USERNAMES. Unchecked, that one-line configuration mistake
        # surfaced as a TypeError inside make_quiz, because every teacher
        # request 403s and `{"detail": ...}` iterates as its keys.
        if response.json().get("role") != "teacher":
            print(
                f"{args.teacher} signed in as a student, not a teacher.\n"
                "Put that username in TEACHER_USERNAMES on the instance, or "
                "pass --teacher to name one that is already there.",
                file=sys.stderr,
            )
            return 2

    # One student, all the way through, before summoning two hundred.
    #
    # Every later failure looks the same in the table — a column of 401s — and
    # says nothing about why. The commonest cause is not load at all: the
    # session cookie is `Secure` when ENVIRONMENT=production, so it is dropped
    # silently by any client talking plain HTTP, and the run then measures
    # nothing but rejections.
    probe = new_client()
    signed_in = await sign_in_student(probe, 0, args.loadtest_key)
    if signed_in.status_code != 200:
        print(
            f"a student could not sign in: HTTP {signed_in.status_code} "
            f"{signed_in.text[:200]}",
            file=sys.stderr,
        )
        return 2
    me = await probe.get("/api/auth/me")
    await probe.aclose()
    if me.status_code != 200:
        scheme = args.base_url.split(":", 1)[0]
        print(
            f"signing in worked, but the session did not stick: /api/auth/me "
            f"returned HTTP {me.status_code}.\n"
            + (
                "The session cookie is Secure when ENVIRONMENT=production, and "
                f"this run is talking {scheme}. Use an https:// base URL.\n"
                if scheme == "http"
                else "Check that the deployment is a single instance: two "
                "processes with different session secrets reject each other's "
                "cookies (compare `instance` and `secret` from /api/health).\n"
            ),
            file=sys.stderr,
        )
        return 2

    quiz_id, questions = await make_quiz(teacher, args.questions)
    made = await teacher.post(f"/api/sessions?quiz_id={quiz_id}&loadtest=true")
    if made.status_code >= 400:
        print(f"could not start a session: HTTP {made.status_code} {made.text[:200]}", file=sys.stderr)
        return 2
    code = made.json()["code"]
    # Printed before anything else happens: if this run dies halfway, this is
    # the code needed to clean up by hand
    # (DELETE /api/sessions/<code>, teacher-only).
    print(f"session {code} (load-test): {args.questions} questions, {args.students} students")

    try:
        return await drive_the_lecture(args, recorder, new_client, teacher, code, quiz_id, questions)
    finally:
        # In a `finally`, because the first real run against the deployment
        # died mid-arrival and left the session and two hundred throwaway
        # students in the database — the one outcome this whole flow exists to
        # avoid. A crash is exactly when cleanup matters most.
        await purge(args, teacher, code)
        await teacher.aclose()


async def drive_the_lecture(
    args: argparse.Namespace,
    recorder: Recorder,
    new_client,
    teacher: httpx.AsyncClient,
    code: str,
    quiz_id: int,
    questions: list[dict],
) -> int:
    pool_readings: list[dict] = []
    watching = asyncio.Event()
    watcher = asyncio.create_task(watch_health(new_client(), watching, pool_readings))

    # --- students arrive ---------------------------------------------------
    students = [new_client() for _ in range(args.students)]
    stop = asyncio.Event()
    streams: list[asyncio.Task] = []

    # How many students may be mid-arrival at once.
    #
    # Arrivals are staggered over `--arrival-seconds`, which is enough while
    # the server keeps up. When it does not — and finding that out is the
    # point — each arrival takes longer, the next one starts anyway, and they
    # pile up until this machine has no socket left for the next connection.
    # That surfaces as `ConnectError: All connection attempts failed`, which
    # reads like the server refusing us and is the load generator falling over
    # instead. The queue belongs here, where it is bounded.
    arriving = asyncio.Semaphore(args.arrival_concurrency)

    async def arrive(index: int, client: httpx.AsyncClient) -> None:
        # The class does not arrive in lockstep; spread them over the recruiting
        # window so this measures a lecture rather than a thundering herd that
        # never happens.
        await asyncio.sleep(args.arrival_seconds * index / max(1, args.students))
        async with arriving:
            # The page first, as a phone does — the app has to be loadable
            # before any of the API traffic below can happen at all.
            if args.page_load:
                await load_the_page(client, f"/s/{code}", recorder)
            await recorder.timed(
                "student login", lambda: sign_in_student(client, index, args.loadtest_key)
            )
            await recorder.timed(
                "student state", lambda: client.get(f"/api/sessions/{code}/state")
            )
        # Outside the semaphore: the stream is held for the whole lecture, so
        # counting it as "arriving" would stop anyone else ever arriving.
        streams.append(asyncio.create_task(hold_stream(client, code, recorder, stop)))

    await asyncio.gather(*(arrive(i, c) for i, c in enumerate(students)))
    await asyncio.sleep(0.5)
    try:
        joined = (
            await stubborn(lambda: teacher.get(f"/api/sessions/{code}/participants"))
        ).json()
        print(f"joined {joined['joined']}, streams open {joined['connected']}")
    except Exception as e:  # noqa: BLE001 — informational; never worth the run
        print(f"could not read the participant count: {e!r}", file=sys.stderr)

    # --- the bouts ---------------------------------------------------------
    for question in questions:
        for phase in ("pre", "post"):
            round_ = await recorder.timed(
                "teacher open round",
                lambda: teacher.post(
                    f"/api/sessions/{code}/rounds",
                    json={"question_id": question["id"], "phase": phase},
                ),
            )
            if round_ is None or round_.status_code >= 400:
                continue
            round_id = round_.json()["id"]

            async def answer(index: int, client: httpx.AsyncClient) -> None:
                # A burst, but not a single instant: phones are thumbed over a
                # few seconds. Anything shorter than the real window would
                # invent contention the lecture does not have.
                await asyncio.sleep(args.burst_seconds * index / max(1, args.students))
                choice = question["choices"][index % len(question["choices"])]
                await recorder.timed(
                    "student answer",
                    lambda: client.post(
                        f"/api/sessions/{code}/answers", json={"choice_id": choice["id"]}
                    ),
                )

            async def teacher_during_burst() -> None:
                """The teacher does not sit still while answers arrive.

                They watch the count come in — and, in the lecture that
                prompted this, refreshed the view mid-burst.
                """
                for tick in range(args.burst_ticks):
                    await recorder.timed(
                        "teacher live", lambda: teacher.get(f"/api/sessions/{code}/live")
                    )
                    if args.refresh_mid_burst and tick == args.burst_ticks // 2:
                        await teacher_view_refresh(teacher, code, quiz_id, questions, recorder)
                    await asyncio.sleep(args.burst_seconds / max(1, args.burst_ticks))

            await asyncio.gather(
                *(answer(i, c) for i, c in enumerate(students)), teacher_during_burst()
            )
            await recorder.timed(
                "teacher close round",
                lambda: teacher.post(f"/api/sessions/{code}/rounds/{round_id}/close"),
            )
            # Between bouts the teacher is on the Report view, which reloads
            # every comparison on each SSE event.
            await teacher_view_refresh(teacher, code, quiz_id, questions, recorder)

    # --- the reports afterwards -------------------------------------------
    await recorder.timed(
        "teacher participation", lambda: teacher.get(f"/api/sessions/{code}/participation")
    )
    await recorder.timed(
        "teacher canvas csv",
        lambda: teacher.get(f"/api/sessions/{code}/canvas-participation.csv"),
    )

    stop.set()
    watching.set()
    watcher.cancel()
    await asyncio.gather(watcher, return_exceptions=True)
    for task in streams:
        task.cancel()
    await asyncio.gather(*streams, return_exceptions=True)
    await asyncio.gather(*(c.aclose() for c in students), return_exceptions=True)

    try:
        health = await stubborn(lambda: teacher.get("/api/health"))
    except Exception:  # noqa: BLE001 — the report below copes with not knowing
        health = None

    report(recorder)
    checked_out = [r.get("checked_out") for r in pool_readings]
    unreachable = sum(1 for c in checked_out if c is None)
    live = [c for c in checked_out if c is not None]
    print(
        f"\ndb pool: peak {max(live, default=0)} of "
        f"{(health.json().get('db_pool', {}) if health is not None else {}).get('size', '?')} checked out over "
        f"{len(pool_readings)} samples; health unanswered {unreachable}x"
    )
    if health is not None:
        print(f"health after the run: {json.dumps(health.json())[:250]}")

    failures = sum(1 for s in recorder.samples if s.failed)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--students", type=int, default=150)
    parser.add_argument("--questions", type=int, default=4)
    parser.add_argument("--teacher", default="teacher")
    parser.add_argument(
        "--arrival-seconds", type=float, default=20.0, help="how long the class takes to arrive"
    )
    parser.add_argument(
        "--burst-seconds", type=float, default=5.0, help="how long answering a bout takes"
    )
    parser.add_argument("--burst-ticks", type=int, default=6, help="teacher /live polls per bout")
    parser.add_argument(
        "--no-refresh-mid-burst",
        dest="refresh_mid_burst",
        action="store_false",
        help="leave out the teacher refresh that made this app slow in class",
    )
    parser.add_argument(
        "--no-page-load",
        dest="page_load",
        action="store_false",
        help="skip the HTML and built assets; drive only the API",
    )
    parser.add_argument(
        "--loadtest-key",
        default=os.environ.get("QUIZBINF_LOADTEST_KEY", ""),
        help=(
            "LOADTEST_KEY of a deployment that has real students in it, so this "
            "run signs in throwaway students instead of needing mock login. "
            "Defaults to $QUIZBINF_LOADTEST_KEY, which is where it belongs — it "
            "is a credential and a command line is not private."
        ),
    )
    parser.add_argument(
        "--teacher-cookie",
        default=os.environ.get("QUIZBINF_TEACHER_COOKIE", ""),
        help=(
            f"value of the `{SESSION_COOKIE}` cookie from a browser already "
            "logged in as the teacher, for a deployment whose only login is the "
            "IdP. Defaults to $QUIZBINF_TEACHER_COOKIE. It is a bearer token "
            "for that person's account, good for up to SESSION_MAX_AGE (a "
            "week) — and logging out does NOT revoke it, because it is signed "
            "rather than stored, so anyone holding a copy stays logged in. "
            "Keep it out of files and shell history; rotating SESSION_SECRET "
            "is the only way to invalidate one, and that signs everybody out."
        ),
    )
    parser.add_argument(
        "--no-purge",
        dest="purge",
        action="store_false",
        help="keep the session and its throwaway students afterwards",
    )
    parser.add_argument(
        "--arrival-concurrency",
        type=int,
        default=25,
        help=(
            "how many students may be loading the page and signing in at once. "
            "Bounds this machine's own sockets when the server slows down; "
            "raise it to push harder, if the client can take it."
        ),
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
