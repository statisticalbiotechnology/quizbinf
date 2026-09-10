"""In-memory SSE broadcaster, one channel per session code.

Good enough for a single-replica deployment (SciLifeLab Serve). If the app is
ever scaled beyond one replica, replace this with Postgres LISTEN/NOTIFY or
Redis pub/sub.
"""

import asyncio
from collections import defaultdict


class Broadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, session_code: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[session_code].add(queue)
        return queue

    def unsubscribe(self, session_code: str, queue: asyncio.Queue) -> None:
        self._subscribers[session_code].discard(queue)
        if not self._subscribers[session_code]:
            del self._subscribers[session_code]

    def connected(self, session_code: str) -> int:
        """Open streams for a session — roughly, clients following right now."""
        return len(self._subscribers.get(session_code, ()))

    def total_open(self) -> int:
        """Every stream this process is holding, across all sessions.

        Reported by `/api/health` because a stream is invisible from
        everywhere else: it is exempt from the concurrency cap, holds no
        database connection, and appears in no request log once established.
        A count that stays high after everyone has gone home is a leak — the
        client vanished and the disconnect never reached us, which through a
        reverse proxy is entirely possible — and each of those wakes on its
        keep-alive timer forever. Diagnosing that from outside was impossible
        without this number.
        """
        return sum(len(queues) for queues in self._subscribers.values())

    async def publish(self, session_code: str, payload: dict) -> None:
        for queue in list(self._subscribers.get(session_code, ())):
            queue.put_nowait(payload)


broadcaster = Broadcaster()
