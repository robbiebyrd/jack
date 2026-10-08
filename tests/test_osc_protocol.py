import dataclasses
import math

import pytest

from jack.show.control.commands import CommandError, RestCommand, SetMouthMode, SetValue, StartPose
from jack.show.control.control_board import ControlBoard
from jack.show.control.osc_feedback import MAX_SUBSCRIBERS, Subscribers, state_messages
from jack.show.control.osc_protocol import (
    OscContext,
    Ping,
    StatusRequest,
    Subscribe,
    Unsubscribe,
    handle_osc,
    osc_command,
)
from tests.fakes import FakeClock
from tests.profiles import PROFILES

SENDER = ("10.10.0.22", 9000)


def osc(address, *args):
    return osc_command(address, list(args), PROFILES)


def board(mode="live"):
    return ControlBoard(PROFILES, 0.5, mode, FakeClock())


def osc_context(board, clock, reply_port=None):
    sent = []
    context = OscContext(
        profiles=PROFILES, board=board, subscribers=Subscribers(clock),
        send=lambda dest, msgs: sent.append((dest, msgs)),
        mumble_connected=lambda: True, reply_port=reply_port,
    )
    return context, sent


def test_osc_routes():
    assert osc("/jack/hand", 0.5) == SetValue("hand", 0.5, 0.5)
    assert osc("/jack/hand/pose", "curl") == StartPose("hand", "curl", None)
    assert osc("/jack/hand/pose", "curl", 2) == StartPose("hand", "curl", 2.0)
    assert osc("/jack/hand/rest") == RestCommand("hand")
    assert osc("/jack/rest") == RestCommand(None)
    assert osc("/jack/mouth/mode", "show") == SetMouthMode("show")


def test_values_are_clamped_to_the_motors_range():
    assert osc("/jack/hand", 1.4) == SetValue("hand", 1.0, 1.4)
    assert osc("/jack/elbow", -0.2) == SetValue("elbow", 0.0, -0.2)
    assert osc("/jack/hand", -1.5) == SetValue("hand", -1.0, -1.5)
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
        ("/jack/rest", [1, 1], 400),
    ],
)
def test_bad_osc_is_rejected_with_a_status(address, args, status):
    with pytest.raises(CommandError) as error:
        osc_command(address, args, PROFILES)
    assert error.value.status == status


def test_handle_osc_applies_good_messages_and_logs_bad_ones_rate_limited(journal):
    b, clock = board(), FakeClock()
    caplog = journal(clock)
    context, _ = osc_context(b, clock)
    handle_osc("/jack/hand", [1.0], SENDER, context)
    assert b.target_volts("hand") == 2.0
    handle_osc("/jack/hand", ["oops"], SENDER, context)
    handle_osc("/jack/hand", ["oops"], SENDER, context)
    handle_osc("/jack/mouth", [0.5], SENDER, context)
    handle_osc("/jack/hand", [3.0], SENDER, context)
    assert len(caplog.messages) == 3
    assert "Ignored OSC /jack/hand" in caplog.messages[0]
    assert "live mode" in caplog.messages[1]
    assert "Clamped OSC /jack/hand 3.0 to 1.0" in caplog.messages[2]


def logging_context(journal):
    clock = FakeClock()
    context, _ = osc_context(board(), clock)
    return journal(clock), context


def test_bad_addresses_of_one_kind_share_a_log_line(journal):
    caplog, context = logging_context(journal)
    handle_osc("/jack/x1", [1], SENDER, context)
    handle_osc("/jack/x2", [1], SENDER, context)
    assert len(caplog.messages) == 1
    handle_osc("/jack/mouth", [0.5], SENDER, context)
    assert len(caplog.messages) == 2


def test_logged_address_is_truncated(journal):
    caplog, context = logging_context(journal)
    handle_osc("/jack/" + "x" * 494, [1], SENDER, context)
    assert len(caplog.messages[0]) < 600 and "…" in caplog.messages[0]


def test_logged_address_of_a_clamped_value_is_truncated(journal):
    caplog, context = logging_context(journal)
    address = "/jack" + "/" * 1000 + "hand"  # empty parts are skipped, so this still routes to the hand
    handle_osc(address, [3.0], SENDER, context)
    assert caplog.messages[0].startswith("Clamped OSC") and len(caplog.messages[0]) < 600


def test_ping_replies_pong_to_the_senders_source_port():
    context, sent = osc_context(board(), FakeClock())
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(SENDER, [("/jack/pong", [])])]


def test_replies_use_the_configured_reply_port_on_the_senders_ip():
    context, sent = osc_context(board(), FakeClock(), reply_port=21601)
    handle_osc("/jack/ping", [], SENDER, context)
    assert sent == [(("10.10.0.22", 21601), [("/jack/pong", [])])]


def test_status_replies_with_the_full_state():
    b = board()
    context, sent = osc_context(b, FakeClock())
    handle_osc("/jack/status", [], SENDER, context)
    assert sent == [(SENDER, state_messages(b.status(True)))]


def test_subscribe_uses_the_given_port_or_the_reply_port():
    context, _ = osc_context(board(), FakeClock(), reply_port=21601)
    handle_osc("/jack/subscribe", [], SENDER, context)
    handle_osc("/jack/subscribe", [7000], SENDER, context)
    assert {dest for dest, _ in context.subscribers.due(state_messages(board().status(True)))} == {
        ("10.10.0.22", 21601), ("10.10.0.22", 7000)}
    handle_osc("/jack/unsubscribe", [7000], SENDER, context)
    assert len(context.subscribers) == 1


def test_a_refused_subscription_is_logged_once_per_minute(journal):
    clock = FakeClock()
    caplog = journal(clock)
    context, _ = osc_context(board(), clock)
    for port in range(MAX_SUBSCRIBERS + 3):
        handle_osc("/jack/subscribe", [20000 + port], SENDER, context)
    assert len(context.subscribers) == MAX_SUBSCRIBERS
    assert len([line for line in caplog.messages if "subscription" in line]) == 1


@pytest.mark.parametrize("args", [[1023], [70000], ["x"], [True], [1, 2]])
def test_bad_subscribe_ports_are_ignored_and_logged(args, caplog):
    context, _ = osc_context(board(), FakeClock())
    handle_osc("/jack/subscribe", args, SENDER, context)
    assert len(context.subscribers) == 0
    assert caplog.messages and "Ignored OSC /jack/subscribe" in caplog.messages[0]


@pytest.mark.parametrize("address", ["/jack/ping", "/jack/status"])
def test_extra_arguments_to_ping_and_status_are_rejected(address):
    with pytest.raises(CommandError) as error:
        osc_command(address, [1, 1], PROFILES)
    assert error.value.status == 400


@pytest.mark.parametrize("address", ["/jack/ping", "/jack/status"])
def test_a_failed_reply_is_logged_with_its_destination_not_raised(address, caplog):
    def failing_send(dest, msgs):
        raise OSError("network unreachable")

    context, _ = osc_context(board(), FakeClock())
    context = dataclasses.replace(context, send=failing_send)
    handle_osc(address, [], SENDER, context)
    assert caplog.messages == [f"OSC reply to {SENDER} failed: network unreachable"]


def test_unsubscribe_without_a_port_removes_a_subscription_made_without_one():
    context, _ = osc_context(board(), FakeClock())
    handle_osc("/jack/subscribe", [], SENDER, context)
    assert len(context.subscribers) == 1
    handle_osc("/jack/unsubscribe", [], SENDER, context)
    assert len(context.subscribers) == 0


@pytest.mark.parametrize("args", [[21601.0], [21601]])
def test_whole_number_float_ports_are_accepted(args):
    assert osc_command("/jack/subscribe", args, PROFILES) == Subscribe(21601)


@pytest.mark.parametrize("port", [21601.5, 1023.0, 1, 1.0, 70000.0, 65536, float("nan"), float("inf"), True, "21601"])
def test_other_ports_are_rejected(port):
    with pytest.raises(CommandError) as error:
        osc_command("/jack/subscribe", [port], PROFILES)
    assert error.value.status == 400
    assert "port must be a whole number 1024-65535" in str(error.value)


def test_the_lowest_and_highest_ports_are_accepted():
    assert osc_command("/jack/subscribe", [1024.0], PROFILES) == Subscribe(1024)
    assert osc_command("/jack/unsubscribe", [65535], PROFILES) == Unsubscribe(65535)


@pytest.mark.parametrize("address", ["/jack/subscribe", "/jack/unsubscribe"])
@pytest.mark.parametrize("release", [[0], [0.0], [False]])
def test_subscribe_button_releases_are_ignored_silently(address, release, caplog):
    context, sent = osc_context(board(), FakeClock())
    assert osc_command(address, release, PROFILES) is None
    handle_osc(address, release, SENDER, context)
    assert len(context.subscribers) == 0 and caplog.messages == [] and sent == []


@pytest.mark.parametrize("address, args", [
    ("/jack/elbow/pose/up", [math.nan]), ("/jack/rest", [math.inf]), ("/jack/hand/rest", [-math.inf]),
    ("/jack/mouth/mode/show", [math.nan]), ("/jack/mouth/mode", [math.inf]),
])
def test_non_finite_button_values_are_400(address, args):
    with pytest.raises(CommandError) as error:
        osc_command(address, args, PROFILES)
    assert error.value.status == 400
    assert "finite" in str(error.value)


def subscribed_context(clock):
    context, _ = osc_context(board(), clock)
    handle_osc("/jack/subscribe", [21601], SENDER, context)
    return context


def live_subscribers(context):
    """How many subscribers are still due feedback (expired leases are only dropped when feedback is due)."""
    return len(context.subscribers.due(state_messages(board().status(True))))


def test_a_command_from_a_subscribers_ip_renews_its_lease():
    clock = FakeClock()
    context = subscribed_context(clock)
    clock.advance(50.0)
    handle_osc("/jack/hand", [0.5], ("10.10.0.22", 4444), context)
    clock.advance(50.0)
    assert live_subscribers(context) == 1


@pytest.mark.parametrize("address, args", [("/jack/rest", [0]), ("/jack/ping", [])])
def test_ignored_releases_and_queries_renew_too(address, args):
    clock = FakeClock()
    context = subscribed_context(clock)
    clock.advance(50.0)
    handle_osc(address, args, SENDER, context)
    clock.advance(50.0)
    assert live_subscribers(context) == 1


def test_a_rejected_message_does_not_renew():
    clock = FakeClock()
    context = subscribed_context(clock)
    clock.advance(50.0)
    handle_osc("/jack/hand", ["oops"], SENDER, context)
    clock.advance(50.0)
    assert live_subscribers(context) == 0


def test_a_command_from_another_ip_does_not_renew():
    clock = FakeClock()
    context = subscribed_context(clock)
    clock.advance(50.0)
    handle_osc("/jack/hand", [0.5], ("10.10.0.99", 9000), context)
    clock.advance(50.0)
    assert live_subscribers(context) == 0


@pytest.mark.parametrize("address, command", [
    ("/jack/rest", RestCommand(None)),
    ("/jack/hand/rest", RestCommand("hand")),
    ("/jack/ping", Ping()),
    ("/jack/status", StatusRequest()),
    ("/jack/elbow/pose/up", StartPose("elbow", "up", None)),
])
@pytest.mark.parametrize("press", [[], [1], [1.0], [True]])
def test_button_presses_act(address, command, press):
    assert osc_command(address, press, PROFILES) == command


@pytest.mark.parametrize(
    "address", ["/jack/rest", "/jack/hand/rest", "/jack/ping", "/jack/status", "/jack/elbow/pose/up"]
)
@pytest.mark.parametrize("release", [[0], [0.0], [False]])
def test_button_releases_are_ignored(address, release):
    assert osc_command(address, release, PROFILES) is None


@pytest.mark.parametrize(
    "address, args", [("/jack/rest", ["go"]), ("/jack/rest", [1, 1]), ("/jack/elbow/pose/up", ["x"])]
)
def test_bad_button_arguments_are_400(address, args):
    with pytest.raises(CommandError) as error:
        osc_command(address, args, PROFILES)
    assert error.value.status == 400


def test_unknown_pose_address_is_404():
    with pytest.raises(CommandError) as error:
        osc_command("/jack/elbow/pose/wave", [1.0], PROFILES)
    assert error.value.status == 404


@pytest.mark.parametrize("address", ["/jack/mouth/mode", "/jack/mouth/mode/show"])
@pytest.mark.parametrize(
    "value, mode", [(1, "show"), (1.0, "show"), (True, "show"), (0, "live"), (0.0, "live"), (False, "live")]
)
def test_numeric_mouth_mode(address, value, mode):
    assert osc_command(address, [value], PROFILES) == SetMouthMode(mode)


def test_string_mouth_mode_still_works_and_show_address_needs_a_number():
    assert osc_command("/jack/mouth/mode", ["show"], PROFILES) == SetMouthMode("show")
    with pytest.raises(CommandError):
        osc_command("/jack/mouth/mode/show", ["show"], PROFILES)


def test_a_momentary_rest_button_rests_once_and_logs_nothing(caplog):
    b = board()
    b.set_value("hand", 1.0)
    context, sent = osc_context(b, FakeClock())
    handle_osc("/jack/rest", [1.0], SENDER, context)
    assert b.target_volts("hand") is None
    b.set_value("hand", 1.0)
    handle_osc("/jack/rest", [0.0], SENDER, context)
    assert b.target_volts("hand") == 2.0
    assert caplog.messages == [] and sent == []
