"""Is the slowness in the ingress, or in the application?

A deliberately small, readable script — small enough for somebody who has
never seen this codebase to read in one sitting, because the people who need
its answer run the platform rather than the app.

`lecture.py` beside this file simulates a whole class: logging in, holding
server-sent-event streams open, a teacher driving rounds, 150 phones answering
in the same second. That is the right tool for finding out whether the *app*
survives a lecture, and the wrong one for asking whether a request can cross
the ingress promptly, because almost everything it measures also touches the
database, the session cookie and the application's own concurrency limits.

This asks the narrow question instead. It needs **no credentials, no
database, and no setup** — every endpoint it touches is public:

  GET /api/health   an `async def` that reads two in-memory counters. It takes
                    no database connection and is deliberately exempt from the
                    application's concurrency cap, precisely so that it keeps
                    answering while everything else queues. If *this* is slow,
                    the application is not what made it slow.

  GET /             the single-page app's HTML, served from disk.

So a stall here is the network, the ingress, or the platform underneath —
not this app's database, locking or request queue.

What it prints, and why: percentiles, and then **every request slower than
--slow-seconds with its wall-clock time**, so the timestamps can be lined up
against an ingress log. "It was slow sometimes" is not actionable; "these
eleven requests took over five seconds, at these moments" is.

    python -m loadtest.ingress --base-url https://quizbinf.serve.scilifelab.se
    python -m loadtest.ingress --base-url https://… --clients 50 --seconds 120

One caveat, and the script says so itself when it applies: every client runs in
one Python process and shares one interpreter lock, so at high --clients the
requests can queue *here* rather than at the server. `--clients 1` is the
control — a single client cannot queue behind itself, so a slow request there
is unambiguously real.

Exit status is 1 when anything was slower than --slow-seconds, 0 otherwise, so
it can be run from a cron or a check without reading the output.
"""

import argparse
import asyncio
import statistics
import time
from datetime import datetime

import httpx

#: Endpoints that need no login and touch no database. Keep it that way: the
#: entire value of this script is that a slow result here cannot be blamed on
#: the application.
PUBLIC_PATHS = ("/api/health", "/")


class Timing:
    """One request: when it started, how long it took, what came back."""

    __slots__ = ("path", "started", "seconds", "status", "error")

    def __init__(self, path, started, seconds, status, error):
        self.path = path
        self.started = started
        self.seconds = seconds
        self.status = status
        self.error = error


async def one_request(client: httpx.AsyncClient, path: str) -> Timing:
    started_at = datetime.now().astimezone()
    started = time.perf_counter()
    try:
        response = await client.get(path)
        return Timing(path, started_at, time.perf_counter() - started, response.status_code, None)
    except Exception as e:  # noqa: BLE001 - a failure is a measurement
        return Timing(path, started_at, time.perf_counter() - started, 0, repr(e))


async def one_client(base_url: str, seconds: float, timeout: float) -> list[Timing]:
    """Request the public endpoints in a loop until the clock runs out."""
    timings = []
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            for path in PUBLIC_PATHS:
                timings.append(await one_request(client, path))
    return timings


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank, so a p99 over a hundred samples is a real request."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[index]


def report(timings: list[Timing], slow_seconds: float) -> int:
    print(f"\n{'endpoint':<16}{'n':>7}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}{'fail':>7}")
    print("-" * 66)
    for path in PUBLIC_PATHS:
        for_path = [t for t in timings if t.path == path]
        if not for_path:
            continue
        durations = [t.seconds for t in for_path]
        failed = sum(1 for t in for_path if t.error or t.status >= 400)
        print(
            f"{path:<16}{len(for_path):>7}"
            f"{percentile(durations, 0.50):>9.3f}"
            f"{percentile(durations, 0.95):>9.3f}"
            f"{percentile(durations, 0.99):>9.3f}"
            f"{max(durations):>9.3f}"
            f"{failed or '':>7}"
        )

    slow = sorted(
        (t for t in timings if t.seconds >= slow_seconds), key=lambda t: t.started
    )
    if not slow:
        print(f"\nNothing took {slow_seconds}s or more. ", end="")
        worst = max(t.seconds for t in timings) if timings else 0
        print(f"The slowest single request was {worst:.3f}s.")
        return 0

    print(f"\n{len(slow)} request(s) took {slow_seconds}s or more:\n")
    print(f"  {'when':<32}{'endpoint':<16}{'seconds':>9}  result")
    for t in slow:
        outcome = t.error or f"HTTP {t.status}"
        print(f"  {t.started.isoformat(timespec='milliseconds'):<32}{t.path:<16}{t.seconds:>9.3f}  {outcome}")
    print(
        "\nThose timestamps are the useful part: none of these endpoints reads "
        "the database,\nso whatever the delay was, it happened before the "
        "application did any work."
    )
    _warn_if_this_script_is_the_queue(slow)
    return 1


def _warn_if_this_script_is_the_queue(slow: list[Timing]) -> None:
    """Say so when the delay is ours rather than the server's.

    All these clients share one Python process and therefore one interpreter
    lock, so past some concurrency the requests queue *here* and the script
    reports its own back pressure as if the server had caused it. A diagnostic
    that blames the server for the harness is worse than no diagnostic, and
    this one caught itself doing exactly that on its first run.

    The tell is unmistakable once you know it: the slow requests all *finish*
    at nearly the same moment, with durations stepping down evenly, because
    they were waiting in one queue that then drained at once. A server-side
    stall has no reason to line up like that.
    """
    if len(slow) < 3:
        return
    finished = sorted(t.started.timestamp() + t.seconds for t in slow)
    if finished[-1] - finished[0] > 1.0:
        return
    print(
        "\n  NOTE: those requests all finished within a second of each other, "
        "which is what\n  this script looks like when *it* is the bottleneck "
        "— every client shares one\n  Python process and one interpreter lock. "
        "Re-run with --clients 1 to check:\n  a single client cannot queue "
        "behind itself, so anything slow there is real."
    )


async def run(args: argparse.Namespace) -> int:
    print(f"{args.base_url}\n{args.clients} client(s) for {args.seconds}s, no login, no database")

    # One client first, with nothing else running. This is the baseline: what
    # a request costs when the app is idle and nothing is competing.
    alone = await one_client(args.base_url, min(5.0, args.seconds), args.timeout)
    quiet = [t.seconds for t in alone if not t.error]
    if quiet:
        print(
            f"idle baseline: median {statistics.median(quiet):.3f}s "
            f"over {len(quiet)} requests"
        )

    together = await asyncio.gather(
        *(one_client(args.base_url, args.seconds, args.timeout) for _ in range(args.clients))
    )
    timings = alone + [t for batch in together for t in batch]
    return report(timings, args.slow_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--clients", type=int, default=30, help="concurrent clients")
    parser.add_argument("--seconds", type=float, default=60.0, help="how long to keep going")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-request timeout")
    parser.add_argument(
        "--slow-seconds",
        type=float,
        default=2.0,
        help="list every request at least this slow, with its timestamp",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
