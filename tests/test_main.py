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


def test_cycle_is_three_and_a_half_seconds():
    assert cycle_s() == pytest.approx(3.5)


def play_step(profiles, step):
    for motor, counts in profiles:
        motor.drive(counts[step])


def test_motor_b_steps_minus_six_minus_three_zero_while_motor_a_stays_off():
    bus = RecordingBus()
    profiles = main.build_profiles(Pca9685(bus, main.PCA9685_ADDRESS, main.PWM_FREQ_HZ))
    (_, a_counts), (_, b_counts) = profiles
    assert a_counts == [0] * 70
    assert b_counts == [-2048] * 10 + [-1024] * 20 + [0] * 40

    play_step(profiles, 0)
    assert off_count(bus, MOTOR_A.pwm) == 0
    assert (off_count(bus, MOTOR_B.in1), off_count(bus, MOTOR_B.in2)) == (PWM_MAX_COUNT, 0)
    assert off_count(bus, MOTOR_B.pwm) == 2048  # -6 V of 12 V
