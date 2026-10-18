"""In-memory sliding-window rate limiter (production hardening B-09).

State lives in this process only, so the limits hold for a single-worker, single-instance deployment. production_checks
refuses to start a production process configured for several workers; several instances would need shared state
(e.g. Redis), which is deliberately not introduced without an approved multi-instance deployment.
"""

import time
from collections import deque

PRUNE_THRESHOLD = 10_000  # bound memory when many distinct keys appear (e.g. login attempts for random accounts)


class SlidingWindowLimiter:
    def __init__(self, window_seconds: float = 60.0):
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def allow(self, key: str, limit: int, now: float | None = None) -> bool:
        """Record one attempt for `key` and return False if it exceeds `limit` per window. limit <= 0 disables."""
        if limit <= 0:
            return True
        now = time.monotonic() if now is None else now
        if len(self._hits) > PRUNE_THRESHOLD:
            self._prune(now)
        hits = self._hits.setdefault(key, deque())
        while hits and now - hits[0] >= self.window:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()

    def _prune(self, now: float) -> None:
        for key in [key for key, hits in self._hits.items() if not hits or now - hits[-1] >= self.window]:
            del self._hits[key]


login_limiter = SlidingWindowLimiter()
# Password step-up attempts (AIS-12 Copilot, G-11 business API): their own instance, so step-up keys can never collide
# with sign-in keys (`{ip}|{identifier}`).
step_up_limiter = SlidingWindowLimiter()
