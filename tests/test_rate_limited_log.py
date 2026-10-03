from jack.support.rate_limited_log import RateLimitedLog
from tests.fakes import FakeClock


def test_one_line_per_key_per_interval():
    lines, clock = [], FakeClock()
    log = RateLimitedLog(lines.append, 60.0, clock)
    log("a", "first")
    log("a", "again")
    log("b", "other")
    clock.advance(60.0)
    log("a", "later")
    assert lines == ["first", "other", "later"]
