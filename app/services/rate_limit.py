"""Simple per-client sliding-window rate limiter (in memory, single process)."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float = 60.0, max_clients: int = 10_000, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_clients = max_clients
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def check(self, client_id: str) -> float | None:
        """Record a hit. Returns None if allowed, otherwise the seconds until the next slot frees up."""
        if not self.enabled:
            return None
        now = self._clock()
        hits = self._hits.get(client_id)
        if hits is None:
            if len(self._hits) >= self.max_clients:
                self._evict(now)
            hits = self._hits.setdefault(client_id, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return max(0.0, hits[0] + self.window - now)
        hits.append(now)
        return None

    def _evict(self, now: float) -> None:
        for key in [k for k, v in self._hits.items() if not v or v[-1] <= now - self.window]:
            del self._hits[key]
        if len(self._hits) >= self.max_clients:  # still full: drop the oldest client
            self._hits.pop(next(iter(self._hits)))
