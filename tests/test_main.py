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


def test_cycle_is_well_inside_the_watchdog_timeout():
    watchdog_s = 10  # WatchdogSec in deploy/jack.service
    assert main.CYCLE_S * 2 < watchdog_s


def test_cycle_is_two_seconds_of_40_ms_steps():
    assert main.CYCLE_S / main.STEPS_PER_CYCLE == pytest.approx(0.04)


def play_step(profiles, step):
    for motor, counts in profiles:
        motor.drive(counts[step])


def test_motor_a_ramps_to_plus_six_while_motor_b_holds_minus_six():
    bus = RecordingBus()
    profiles = main.build_profiles(Pca9685(bus, main.PCA9685_ADDRESS, main.PWM_FREQ_HZ))

    for step in (0, main.STEPS_PER_CYCLE // 2, main.STEPS_PER_CYCLE - 1):
        play_step(profiles, step)
        assert (off_count(bus, MOTOR_B.in1), off_count(bus, MOTOR_B.in2)) == (PWM_MAX_COUNT, 0)
        assert off_count(bus, MOTOR_B.pwm) == 2048  # -6 V of 12 V

    play_step(profiles, main.STEPS_PER_CYCLE // 2)
    assert (off_count(bus, MOTOR_A.in1), off_count(bus, MOTOR_A.in2)) == (0, PWM_MAX_COUNT)
    assert off_count(bus, MOTOR_A.pwm) == 2048  # +6 V peak
