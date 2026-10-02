"""Logs at most one line per kind per interval, so a misbehaving controller can't flood the journal."""

from collections.abc import Callable


class RateLimitedLog:
    def __init__(self, log: Callable[[str], None], interval_s: float, clock: Callable[[], float]):
        self._log = log
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}

    def __call__(self, key: str, message: str) -> None:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._interval_s:
            return
        self._last[key] = now
        self._log(message)
