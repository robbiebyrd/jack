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


def test_configured_ramp_peaks_at_six_volts_over_a_two_second_cycle():
    counts = main.ramp_profile(main.PEAK_VOLTS, main.SUPPLY_VOLTS, main.STEPS_PER_CYCLE)
    assert max(counts) == 2048
    assert main.CYCLE_S / main.STEPS_PER_CYCLE == pytest.approx(0.04)


def test_motor_a_ramps_positive_and_motor_b_ramps_negative():
    bus = RecordingBus()
    motors = main.build_motors(Pca9685(bus, main.PCA9685_ADDRESS, main.PWM_FREQ_HZ))
    motors.set_forward()
    motors.set_duty(2048)
    assert (off_count(bus, MOTOR_A.in1), off_count(bus, MOTOR_A.in2)) == (0, PWM_MAX_COUNT)
    assert (off_count(bus, MOTOR_B.in1), off_count(bus, MOTOR_B.in2)) == (PWM_MAX_COUNT, 0)
    assert off_count(bus, MOTOR_A.pwm) == 2048
    assert off_count(bus, MOTOR_B.pwm) == 2048
