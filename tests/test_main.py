import signal

import pytest

import main
from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B
from tests.fakes import RecordingBus, off_count


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(notify_socket):
    main.ping_watchdog()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


def cycle_s():
    return len(main.build_profiles(Pca9685(RecordingBus(), main.PCA9685_ADDRESS, main.PWM_FREQ_HZ))[0][1]) * main.STEP_S


def test_cycle_is_well_inside_the_watchdog_timeout():
    watchdog_s = 10  # WatchdogSec in deploy/jack.service
    assert cycle_s() * 2 < watchdog_s


def test_demo_cycle_is_four_and_a_quarter_seconds():
    assert cycle_s() == pytest.approx(4.25)


def play_step(profiles, step):
    for motor, counts in profiles:
        motor.drive(counts[step])


def test_demo_closes_rests_relaxes_rests_then_opens_fully_with_motor_a_off():
    bus = RecordingBus()
    (_, a_counts), (_, b_counts) = main.build_profiles(Pca9685(bus, main.PCA9685_ADDRESS, main.PWM_FREQ_HZ))
    assert b_counts == (
        [341] * 5  # close: +1 V for 0.25 s
        + [0] * 30  # rest 1.5 s
        + [-683] * 10  # relax: -2 V for 0.5 s
        + [0] * 30  # rest 1.5 s
        + [-2048] * 10  # open fully: -6 V for 0.5 s
    )
    assert a_counts == [0] * len(b_counts)
