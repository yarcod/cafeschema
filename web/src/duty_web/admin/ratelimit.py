"""A sliding-window limit per key, in memory.

One Fly machine runs the app, so process memory is the whole picture. The
point is to stop a confused agent or a stuck form from hammering SQLite,
not to defend against a distributed attack.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Hashable


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[Hashable, deque[float]] = {}

    def allow(self, key: Hashable, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


api_limiter = RateLimiter(120, 60)
admin_limiter = RateLimiter(60, 60)
