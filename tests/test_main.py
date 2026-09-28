import random
import signal

import pytest

import main
from motor_test.mouth import STEP_S
from motor_test.ramp import segment_profile
from motor_test.speech import MAX_PHRASE_S, random_phrase


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(notify_socket):
    main.ping_watchdog()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


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
