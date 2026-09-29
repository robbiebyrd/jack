import random
import signal
import socket

import pytest

import main
from motor_test.mouth import STEP_S
from motor_test.ramp import segment_profile
from motor_test.speech import MAX_PHRASE_S, random_phrase
from motor_test.talk_settings import TalkSettings


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(notify_socket):
    main.ping_watchdog()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


class StandInClient:
    def __init__(self, alive: bool):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_watchdog_while_connected_pings_while_the_mumble_client_lives(notify_socket):
    main.watchdog_while_connected(StandInClient(alive=True))()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


def test_watchdog_while_connected_exits_without_pinging_once_the_mumble_client_died(notify_socket):
    with pytest.raises(SystemExit) as exit_info:
        main.watchdog_while_connected(StandInClient(alive=False))()
    assert "Mumble" in str(exit_info.value.code)
    with pytest.raises(socket.timeout):
        notify_socket.recv(64)


WATCHDOG_S = 10  # WatchdogSec in deploy/jack.service


def test_demo_cycle_is_four_and_a_half_seconds_inside_the_watchdog():
    motor_a, motor_b = main.demo_cycle()
    assert len(motor_b) * STEP_S == pytest.approx(4.5)
    assert len(motor_b) * STEP_S * 2 < WATCHDOG_S


def test_demo_closes_rests_relaxes_rests_then_opens_fully_with_motor_a_off():
    motor_a, motor_b = main.demo_cycle()
    assert motor_b == (
        [341] * 5  # close: +1 V for 0.25 s
        + [0] * 30  # rest 1.5 s
        + [-683] * 10  # relax: -2 V for 0.5 s
        + [0] * 30  # rest 1.5 s
        + [-410, -819, -1229, -1638, -2048]  # open fully: ramp 0 -> -6 V over 0.25 s
        + [-2048] * 10  # then hold -6 V for 0.5 s
    )
    assert motor_a == [0] * len(motor_b)


@pytest.mark.parametrize("seed", range(20))
def test_speaking_cycle_plays_a_random_phrase_on_the_mouth_with_motor_a_off(seed):
    motor_a, motor_b = main.speaking_cycle(random.Random(seed))
    assert motor_b == segment_profile(random_phrase(random.Random(seed)), main.SUPPLY_VOLTS, STEP_S)
    assert motor_a == [0] * len(motor_b)


def test_speaking_phrases_ping_the_watchdog_in_time():
    assert MAX_PHRASE_S * 2 < WATCHDOG_S


def test_consecutive_speaking_cycles_differ():
    rng = random.Random(1)
    assert main.speaking_cycle(rng) != main.speaking_cycle(rng)


def test_mumble_password_comes_from_the_environment():
    assert main.mumble_password({"JACK_MUMBLE_PASSWORD": "s3cret"}) == "s3cret"


@pytest.mark.parametrize("environ", [{}, {"JACK_MUMBLE_PASSWORD": ""}])
def test_missing_mumble_password_exits_with_where_to_set_it(environ):
    with pytest.raises(SystemExit) as exit_info:
        main.mumble_password(environ)
    assert "JACK_MUMBLE_PASSWORD" in str(exit_info.value.code)
    assert "/etc/jack/jack.env" in str(exit_info.value.code)


def test_no_overrides_gives_the_default_settings():
    assert main.talk_settings({}) == TalkSettings()


def test_env_overrides_set_only_the_named_fields():
    settings = main.talk_settings({"JACK_GATE_OPEN_DB": "-20", "JACK_OPEN_CURVE": "1.5"})
    assert settings == TalkSettings(gate_open_db=-20.0, open_curve=1.5)


def test_env_variables_that_are_not_settings_are_ignored():
    assert main.talk_settings({"JACK_MUMBLE_PASSWORD": "s3cret"}) == TalkSettings()


def test_empty_override_keeps_the_default():
    assert main.talk_settings({"JACK_GATE_OPEN_DB": ""}) == TalkSettings()


def test_override_that_is_not_a_number_exits_naming_the_variable():
    with pytest.raises(SystemExit) as exit_info:
        main.talk_settings({"JACK_GATE_OPEN_DB": "abc"})
    assert exit_info.value.code == "JACK_GATE_OPEN_DB='abc' in /etc/jack/jack.env is not a number"


def test_impossible_override_exits_saying_the_settings_are_invalid():
    with pytest.raises(SystemExit) as exit_info:
        main.talk_settings({"JACK_GATE_OPEN_DB": "-40"})
    assert "Mouth settings from /etc/jack/jack.env are invalid" in exit_info.value.code


def test_override_beyond_the_supply_exits_saying_the_settings_are_invalid():
    with pytest.raises(SystemExit) as exit_info:
        main.talk_settings({"JACK_OPEN_MAX_V": "13"})
    assert "Mouth settings from /etc/jack/jack.env are invalid" in exit_info.value.code
    assert "open_max_v" in exit_info.value.code


def test_applied_overrides_are_listed_for_the_log():
    assert main.describe_overrides(TalkSettings(gate_open_db=-20.0, open_curve=1.5)) == (
        "open_curve=1.5, gate_open_db=-20.0"
    )
    assert main.describe_overrides(TalkSettings()) == ""
