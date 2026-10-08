"""A logging filter that lets one record per kind through per interval, so one bad controller can't flood the journal.

A log call names its kind with `rate_limited(key)`: `log.warning("Ignored %s", what, **rate_limited("ignored 404"))`.
Records without a kind always pass. main.py installs the filter on the app's one handler (logging_setup.py).
"""

import logging
from collections.abc import Callable

RATE_KEY = "rate_key"


def rate_limited(key: str) -> dict[str, dict[str, str]]:
    """The keyword arguments that mark a log call as one of kind `key` for RateLimit."""
    return {"extra": {RATE_KEY: key}}


class RateLimit(logging.Filter):
    def __init__(self, interval_s: float, clock: Callable[[], float]):
        super().__init__()
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = getattr(record, RATE_KEY, None)
        if key is None:
            return True
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._interval_s:
            return False
        self._last[key] = now
        return True
