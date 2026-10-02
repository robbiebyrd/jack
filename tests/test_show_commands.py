import dataclasses
import math

import pytest

from motor_test.control_board import ControlBoard
from motor_test.osc_feedback import MAX_SUBSCRIBERS, Subscribers, state_messages
from motor_test.rate_limited_log import RateLimitedLog
from motor_test.show_commands import (
    CommandError, OscContext, RestCommand, SetMouthMode, SetValue, StartPose, apply, handle_osc, http_command,
    osc_command,
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


SENDER = ("10.10.0.22", 9000)


def osc_context(board, lines, clock, reply_port=None):
    sent = []
    context = OscContext(
        profiles=PROFILES, board=board, subscribers=Subscribers(clock), send=lambda dest, msgs: sent.append((dest, msgs)),
        mumble_connected=lambda: True, reply_port=reply_port, log=RateLimitedLog(lines.append, 60.0, clock),
    )
    return context, sent


def test_handle_osc_applies_good_messages_and_logs_bad_ones_rate_limited():
    b, lines = board(), []
    context, _ = osc_context(b, lines, FakeClock())
    handle_osc("/jack/hand", [1.0], SENDER, context)
    assert b.target_volts("hand") == 2.0
    handle_osc("/jack/hand", ["oops"], SENDER, context)
    handle_osc("/jack/hand", ["oops"], SENDER, context)
    handle_osc("/jack/mouth", [0.5], SENDER, context)
    handle_osc("/jack/hand", [3.0], SENDER, context)
    assert len(lines) == 3
    assert "Ignored OSC /jack/hand" in lines[0]
    assert "live mode" in lines[1]
    assert "Clamped OSC /jack/hand 3.0 to 1.0" in lines[2]


def logging_context():
    lines = []
    context, _ = osc_context(board(), lines, FakeClock())
    return lines, context


def test_bad_addresses_of_one_kind_share_a_log_line():
    lines, context = logging_context()
    handle_osc("/jack/x1", [1], SENDER, context)
    handle_osc("/jack/x2", [1], SENDER, context)
    assert len(lines) == 1
    handle_osc("/jack/mouth", [0.5], SENDER, context)
    assert len(lines) == 2


def test_logged_address_is_truncated():
    lines, context = logging_context()
    handle_osc("/jack/" + "x" * 494, [1], SENDER, context)
    assert len(lines[0]) < 600 and "…" in lines[0]


def test_logged_address_of_a_clamped_value_is_truncated():
    lines, context = logging_context()
    address = "/jack" + "/" * 1000 + "hand"  # empty parts are skipped, so this still routes to the hand
    handle_osc(address, [3.0], SENDER, context)
    assert lines[0].startswith("Clamped OSC") and len(lines[0]) < 600


def test_ping_replies_pong_to_the_senders_source_port():
    lines, clock = [], FakeClock()
    context, sent = osc_context(board(), lines, clock)
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(SENDER, [("/jack/pong", [])])]


def test_replies_use_the_configured_reply_port_on_the_senders_ip():
    lines, clock = [], FakeClock()
    context, sent = osc_context(board(), lines, clock, reply_port=21601)
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(("10.10.0.22", 21601), [("/jack/pong", [])])]


def test_status_replies_with_the_full_state():
    lines, clock = [], FakeClock()
    b = board()
    context, sent = osc_context(b, lines, clock)
    handle_osc("/jack/status", [], SENDER, context)
    assert sent == [(SENDER, state_messages(b.status(True)))]


def test_subscribe_uses_the_given_port_or_the_reply_port():
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock, reply_port=21601)
    handle_osc("/jack/subscribe", [], SENDER, context)
    handle_osc("/jack/subscribe", [7000], SENDER, context)
    assert {dest for dest, _ in context.subscribers.due(state_messages(board().status(True)))} == {
        ("10.10.0.22", 21601), ("10.10.0.22", 7000)}
    handle_osc("/jack/unsubscribe", [7000], SENDER, context)
    assert len(context.subscribers) == 1


def test_a_refused_subscription_is_logged_once_per_minute():
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock)
    for port in range(MAX_SUBSCRIBERS + 3):
        handle_osc("/jack/subscribe", [20000 + port], SENDER, context)
    assert len(context.subscribers) == MAX_SUBSCRIBERS
    assert len([line for line in lines if "subscription" in line]) == 1


@pytest.mark.parametrize("args", [[0], [70000], ["x"], [True], [1, 2]])
def test_bad_subscribe_ports_are_ignored_and_logged(args):
    lines, clock = [], FakeClock()
    context, _ = osc_context(board(), lines, clock)
    handle_osc("/jack/subscribe", args, SENDER, context)
    assert len(context.subscribers) == 0
    assert lines and "Ignored OSC /jack/subscribe" in lines[0]


@pytest.mark.parametrize("address", ["/jack/ping", "/jack/status"])
def test_extra_arguments_to_ping_and_status_are_rejected(address):
    with pytest.raises(CommandError) as error:
        osc_command(address, [1], PROFILES)
    assert error.value.status == 400


@pytest.mark.parametrize("path", ["/ping", "/status", "/subscribe", "/unsubscribe"])
def test_osc_only_routes_are_not_http_commands(path):
    with pytest.raises(CommandError) as error:
        http_command(path, {}, PROFILES)
    assert error.value.status == 404


def test_a_failed_reply_is_logged_not_raised():
    lines, clock = [], FakeClock()

    def failing_send(dest, msgs):
        raise OSError("network unreachable")

    context, _ = osc_context(board(), lines, clock)
    context = dataclasses.replace(context, send=failing_send)
    handle_osc("/jack/ping", [], SENDER, context)
    assert lines and "reply" in lines[0].lower()


def test_integer_too_large_for_a_float_is_a_400():
    with pytest.raises(CommandError) as error:
        http_command("/hand", {"value": 10**400}, PROFILES)
    assert error.value.status == 400
