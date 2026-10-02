import math

import pytest

from motor_test.control_board import ControlBoard
from motor_test.rate_limited_log import RateLimitedLog
from motor_test.show_commands import (
    CommandError, RestCommand, SetMouthMode, SetValue, StartPose, apply, handle_osc, http_command, osc_command,
)
from tests.fakes import FakeClock
from tests.profiles import PROFILES


def osc(address, *args):
    return osc_command(address, list(args), PROFILES)


def http(path, body=None):
    return http_command(path, body or {}, PROFILES)


def test_osc_routes():
    assert osc("/jack/hand", 0.5) == SetValue("hand", 0.5, 0.5)
    assert osc("/jack/hand/pose", "curl") == StartPose("hand", "curl", None)
    assert osc("/jack/hand/pose", "curl", 2) == StartPose("hand", "curl", 2.0)
    assert osc("/jack/hand/rest") == RestCommand("hand")
    assert osc("/jack/rest") == RestCommand(None)
    assert osc("/jack/mouth/mode", "show") == SetMouthMode("show")


def test_http_routes_match_osc():
    assert http("/hand", {"value": 0.5}) == SetValue("hand", 0.5, 0.5)
    assert http("/hand/pose", {"name": "curl", "seconds": 2}) == StartPose("hand", "curl", 2.0)
    assert http("/hand/rest") == RestCommand("hand")
    assert http("/rest") == RestCommand(None)
    assert http("/mouth/mode", {"mode": "live"}) == SetMouthMode("live")


def test_values_are_clamped_to_the_motors_range():
    assert osc("/jack/hand", 1.4) == SetValue("hand", 1.0, 1.4)
    assert osc("/jack/hand", -0.2) == SetValue("hand", 0.0, -0.2)
    assert osc("/jack/pivot", -1.5) == SetValue("pivot", -1.0, -1.5)


@pytest.mark.parametrize(
    "address, args, status",
    [
        ("/jack/tail", [0.5], 404),
        ("/other/hand", [0.5], 404),
        ("/jack/hand/wave", [], 404),
        ("/jack/hand/pose", ["wave"], 404),
        ("/jack/hand", [], 400),
        ("/jack/hand", ["half"], 400),
        ("/jack/hand", [True], 400),
        ("/jack/hand", [math.nan], 400),
        ("/jack/hand", [0.5, 0.6], 400),
        ("/jack/hand/pose", [], 400),
        ("/jack/hand/pose", ["curl", 0], 400),
        ("/jack/mouth/mode", ["auto"], 400),
        ("/jack/rest", [1], 400),
    ],
)
def test_bad_osc_is_rejected_with_a_status(address, args, status):
    with pytest.raises(CommandError) as error:
        osc_command(address, args, PROFILES)
    assert error.value.status == status


def test_unexpected_http_fields_are_rejected():
    with pytest.raises(CommandError) as error:
        http("/hand", {"value": 0.5, "speed": 2})
    assert error.value.status == 400 and "speed" in str(error.value)


def board(mode="live"):
    return ControlBoard(PROFILES, 0.5, mode, FakeClock())


def test_apply_drives_the_board():
    b = board()
    apply(SetValue("hand", 1.0, 1.0), b)
    assert b.target_volts("hand") == 2.0
    apply(StartPose("elbow", "up", None), b)
    assert b.target_volts("elbow") == 2.0
    apply(RestCommand(None), b)
    assert b.target_volts("hand") is None
    apply(SetMouthMode("show"), b)
    assert b.mouth_mode == "show"


@pytest.mark.parametrize("command", [SetValue("mouth", 0.5, 0.5), StartPose("mouth", "open", None)])
def test_mouth_commands_conflict_with_live_mode(command):
    b = board("live")
    with pytest.raises(CommandError) as error:
        apply(command, b)
    assert error.value.status == 409
    assert b.target_volts("mouth") is None


def test_mouth_commands_work_in_show_mode():
    b = board("show")
    apply(SetValue("mouth", 1.0, 1.0), b)
    assert b.target_volts("mouth") == -6.0


def test_handle_osc_applies_good_messages_and_logs_bad_ones_rate_limited():
    b, lines, clock = board(), [], FakeClock()
    log = RateLimitedLog(lines.append, 60.0, clock)
    handle_osc("/jack/hand", [1.0], PROFILES, b, log)
    assert b.target_volts("hand") == 2.0
    handle_osc("/jack/hand", ["oops"], PROFILES, b, log)
    handle_osc("/jack/hand", ["oops"], PROFILES, b, log)
    handle_osc("/jack/mouth", [0.5], PROFILES, b, log)
    handle_osc("/jack/hand", [3.0], PROFILES, b, log)
    assert len(lines) == 3
    assert "Ignored OSC /jack/hand" in lines[0]
    assert "live mode" in lines[1]
    assert "Clamped OSC /jack/hand 3.0 to 1.0" in lines[2]


def logging_handler():
    lines = []
    return lines, RateLimitedLog(lines.append, 60.0, FakeClock())


def test_bad_addresses_of_one_kind_share_a_log_line():
    lines, log = logging_handler()
    handle_osc("/jack/x1", [1], PROFILES, board(), log)
    handle_osc("/jack/x2", [1], PROFILES, board(), log)
    assert len(lines) == 1
    handle_osc("/jack/mouth", [0.5], PROFILES, board(), log)
    assert len(lines) == 2


def test_logged_address_is_truncated():
    lines, log = logging_handler()
    handle_osc("/jack/" + "x" * 494, [1], PROFILES, board(), log)
    assert len(lines[0]) < 600 and "…" in lines[0]


def test_integer_too_large_for_a_float_is_a_400():
    with pytest.raises(CommandError) as error:
        http_command("/hand", {"value": 10**400}, PROFILES)
    assert error.value.status == 400
