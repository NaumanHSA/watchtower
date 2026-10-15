from __future__ import annotations

import math
import time
from collections import deque


class FailureLimiter:
    """Counts failed authentications per client address over a sliding window.

    In memory and per replica: enough to make online token guessing impractical. The
    client address is only as trustworthy as FORWARDED_ALLOW_IPS lets it be.
    """

    MAX_TRACKED = 50_000

    def __init__(self, limit: int, window_seconds: float):
        self.limit = limit
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = {}
        self.failures_total = 0

    def _recent(self, key: str, now: float) -> deque[float]:
        entries = self._failures.get(key)
        if entries is None:
            return deque()
        while entries and entries[0] <= now - self.window:
            entries.popleft()
        if not entries:
            del self._failures[key]
        return entries

    def retry_after(self, key: str | None) -> int | None:
        """Seconds until `key` may try again, or None if it isn't blocked."""
        if key is None or self.limit <= 0:
            return None
        now = time.monotonic()
        entries = self._recent(key, now)
        if len(entries) < self.limit:
            return None
        return max(1, math.ceil(entries[0] + self.window - now))

    def record_failure(self, key: str | None) -> None:
        self.failures_total += 1
        if key is None or self.limit <= 0:
            return
        if len(self._failures) >= self.MAX_TRACKED:
            self._failures.clear()  # crude, but bounded memory under a flood
        self._failures.setdefault(key, deque()).append(time.monotonic())
