import logging

from jack.support.rate_limit import rate_limited
from tests.fakes import FakeClock


def test_one_record_per_key_per_interval_and_unkeyed_records_always_pass(journal):
    clock = FakeClock()
    caplog = journal(clock)
    log = logging.getLogger("jack.test_rate_limit")
    log.warning("first", **rate_limited("a"))
    log.warning("again", **rate_limited("a"))
    log.warning("other", **rate_limited("b"))
    log.warning("plain")
    clock.advance(60.0)
    log.warning("later", **rate_limited("a"))
    assert caplog.messages == ["first", "other", "plain", "later"]
