"""How the app and the hand-run tools log: INFO and above to stdout, one line each; journald adds the time."""

import logging
import sys
import time
from collections.abc import Callable

from jack.support.rate_limit import RateLimit

# One journal line per kind of repeated warning (bad OSC messages, roc-recv restarts) per minute.
RATE_LIMIT_S = 60.0
FORMAT = "%(levelname)s %(name)s: %(message)s"


def configure_logging(rate_limit_s: float = RATE_LIMIT_S, clock: Callable[[], float] = time.monotonic) -> None:
    """Send every logger's INFO and above to stdout through the rate limit; safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(FORMAT))
    handler.addFilter(RateLimit(rate_limit_s, clock))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
