"""Opt-in, in-memory idempotency store for ``POST /posting`` (``Idempotency-Key`` header).

* Same key + same message within the TTL -> the cached response is returned; nothing is re-posted.
* Same key + different message -> :class:`IdempotencyConflict` (HTTP 422 ``idempotency_conflict``).
* Concurrent requests with the same key wait for the first one instead of posting twice.
* Bounded size (oldest entries evicted). Single-process only: use Redis for multi-worker deployments.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


class IdempotencyConflict(Exception):
    """The key was already used with a different request body."""


@dataclass
class _Entry(Generic[T]):
    fingerprint: str
    expires_at: float
    future: asyncio.Future[T]


def fingerprint(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


class IdempotencyStore(Generic[T]):
    def __init__(self, ttl_seconds: float, max_entries: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _Entry[T]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def _purge(self) -> None:
        now = self._clock()
        for key in [k for k, e in self._entries.items() if e.expires_at <= now and e.future.done()]:
            del self._entries[key]
        while len(self._entries) > self.max_entries:
            oldest_key = next((k for k, e in self._entries.items() if e.future.done()), None)
            if oldest_key is None:
                break
            del self._entries[oldest_key]

    async def run(self, key: str, message: str, factory: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Return ``(response, replayed)``. ``factory`` runs at most once per key while cached."""
        self._purge()
        fp = fingerprint(message)
        entry = self._entries.get(key)
        if entry is not None:
            if entry.fingerprint != fp:
                raise IdempotencyConflict
            return await asyncio.shield(entry.future), True

        future: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        self._entries[key] = _Entry(fp, self._clock() + self.ttl, future)
        try:
            result = await factory()
        except BaseException as exc:
            # Failed requests are not cached: the client may retry with the same key.
            self._entries.pop(key, None)
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark retrieved to avoid "exception was never retrieved" warnings
            raise
        future.set_result(result)
        self._purge()
        return result, False
