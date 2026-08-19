"""A small in-memory lockout for password guessing.

The teacher password is a single shared secret on a publicly reachable URL,
which is only defensible if guessing it is slow. This keeps a per-client
failure count in memory — consistent with the SSE broadcaster, which already
assumes a single replica; scaling out would need shared state here too.
"""

import time
from threading import Lock

# Allowed failures before the lockout starts, and how long it lasts.
MAX_FAILURES = 5
LOCKOUT_SECONDS = 300


class Throttle:
    def __init__(self, max_failures: int = MAX_FAILURES, lockout: int = LOCKOUT_SECONDS):
        self.max_failures = max_failures
        self.lockout = lockout
        self._lock = Lock()
        # key -> (failure count, when the count started)
        self._failures: dict[str, tuple[int, float]] = {}

    def _now(self) -> float:
        return time.monotonic()

    def locked_for(self, key: str) -> int:
        """Seconds until this key may try again; 0 when it may try now."""
        with self._lock:
            entry = self._failures.get(key)
            if entry is None:
                return 0
            count, started = entry
            if count < self.max_failures:
                return 0
            remaining = self.lockout - (self._now() - started)
            if remaining <= 0:
                del self._failures[key]
                return 0
            return int(remaining) + 1

    def record(self, key: str) -> None:
        """Count one attempt against this key."""
        with self._lock:
            count, started = self._failures.get(key, (0, self._now()))
            # A stale window starts over rather than accumulating for ever.
            if self._now() - started > self.lockout:
                count, started = 0, self._now()
            self._failures[key] = (count + 1, started)

    # Reads better at the password call site, where an attempt *is* a failure.
    record_failure = record

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


teacher_login_throttle = Throttle()

# Roster suggestions: generous enough that typing an address never trips it
# (a keystroke-debounced field makes a handful of calls), tight enough that
# scraping the roster prefix by prefix is slow.
suggest_throttle = Throttle(max_failures=60, lockout=60)
