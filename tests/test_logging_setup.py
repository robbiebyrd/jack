import logging

import pytest

from jack.adapters.system.logging_setup import configure_logging
from tests.fakes import FakeClock


@pytest.fixture
def root_logging_restored():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_info_and_above_go_to_stdout_as_level_logger_message_rate_limited_by_kind(root_logging_restored, capsys):
    configure_logging(rate_limit_s=60.0, clock=FakeClock())
    log = logging.getLogger("jack.adapters.audio.alsa_sink")
    log.debug("not shown")
    log.info("Connected")
    log.warning("Audio underrun #%d; carrying on", 1)
    log.warning("flood", extra={"rate_key": "flood"})
    log.warning("flood", extra={"rate_key": "flood"})
    assert capsys.readouterr().out.splitlines() == [
        "INFO jack.adapters.audio.alsa_sink: Connected",
        "WARNING jack.adapters.audio.alsa_sink: Audio underrun #1; carrying on",
        "WARNING jack.adapters.audio.alsa_sink: flood",
    ]
