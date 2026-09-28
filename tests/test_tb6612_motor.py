import pytest

from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, MotorChannels, Tb6612Motor
from tests.fakes import RecordingBus, off_count

ADDRESS = 0x40


class PwmWriteFailsBus(RecordingBus):
    """RecordingBus that raises OSError on writes to one channel's PWM registers, as from a flaky I2C write."""

    def __init__(self, failing_channel):
        super().__init__()
        base = 0x06 + 4 * failing_channel
        self._failing_registers = {base, base + 1, base + 2, base + 3}

    def write_byte_data(self, i2c_addr, register, value):
        if register in self._failing_registers:
            raise OSError("I2C write failed")
        super().write_byte_data(i2c_addr, register, value)


def make_motor(channels, reverse=False):
    bus = RecordingBus()
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    bus.writes.clear()
    return bus, chip, Tb6612Motor(chip, channels, reverse=reverse)


def test_channel_mapping_matches_waveshare_sample_code():
    assert MOTOR_A == MotorChannels(pwm=0, in1=1, in2=2)
    assert MOTOR_B == MotorChannels(pwm=5, in1=3, in2=4)


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_positive_drive_runs_forward_at_that_duty(channels):
    bus, _, motor = make_motor(channels)
    motor.drive(1024)
    assert (off_count(bus, channels.in1), off_count(bus, channels.in2)) == (0, PWM_MAX_COUNT)
    assert off_count(bus, channels.pwm) == 1024


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_negative_drive_runs_backward_at_that_duty(channels):
    bus, _, motor = make_motor(channels)
    motor.drive(-1024)
    assert (off_count(bus, channels.in1), off_count(bus, channels.in2)) == (PWM_MAX_COUNT, 0)
    assert off_count(bus, channels.pwm) == 1024


def test_zero_drive_holds_forward_with_no_duty():
    bus, _, motor = make_motor(MOTOR_A)
    motor.drive(0)
    assert (off_count(bus, 1), off_count(bus, 2)) == (0, PWM_MAX_COUNT)
    assert off_count(bus, 0) == 0


def test_reverse_flag_swaps_forward_and_backward():
    bus, _, motor = make_motor(MOTOR_B, reverse=True)
    motor.drive(1024)
    assert (off_count(bus, 3), off_count(bus, 4)) == (PWM_MAX_COUNT, 0)
    motor.drive(-1024)
    assert (off_count(bus, 3), off_count(bus, 4)) == (0, PWM_MAX_COUNT)


def test_direction_pins_are_written_only_when_the_direction_changes():
    bus, _, motor = make_motor(MOTOR_B)
    motor.drive(1024)
    bus.writes.clear()
    motor.drive(2048)
    assert [register for _, register, _ in bus.writes] == [0x1A, 0x1B, 0x1C, 0x1D]
    bus.writes.clear()
    motor.drive(-1024)
    assert (off_count(bus, 3), off_count(bus, 4)) == (PWM_MAX_COUNT, 0)
    assert len(bus.writes) == 12


def test_drive_after_stop_restores_the_direction_pins():
    bus, _, motor = make_motor(MOTOR_B)
    motor.drive(1024)
    motor.stop()
    motor.drive(1024)
    assert (off_count(bus, 3), off_count(bus, 4)) == (0, PWM_MAX_COUNT)
    assert off_count(bus, 5) == 1024


def test_two_motors_on_one_chip_keep_separate_channels():
    bus, chip, motor_a = make_motor(MOTOR_A)
    motor_b = Tb6612Motor(chip, MOTOR_B)
    motor_a.drive(2048)
    motor_b.drive(-1000)
    assert off_count(bus, 0) == 2048
    assert off_count(bus, 5) == 1000
    assert (off_count(bus, 1), off_count(bus, 2)) == (0, PWM_MAX_COUNT)
    assert (off_count(bus, 3), off_count(bus, 4)) == (PWM_MAX_COUNT, 0)


def test_stop_zeroes_duty_before_short_braking():
    bus, _, motor = make_motor(MOTOR_B)
    motor.drive(2048)
    bus.writes.clear()
    motor.stop()
    assert bus.writes[:4] == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x00),
    ]
    assert off_count(bus, 5) == 0
    assert off_count(bus, 3) == PWM_MAX_COUNT
    assert off_count(bus, 4) == PWM_MAX_COUNT


def test_stop_still_short_brakes_when_zeroing_duty_fails():
    bus = PwmWriteFailsBus(MOTOR_B.pwm)
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    motor = Tb6612Motor(chip, MOTOR_B)
    with pytest.raises(OSError, match="I2C write failed"):
        motor.stop()
    assert off_count(bus, MOTOR_B.in1) == PWM_MAX_COUNT
    assert off_count(bus, MOTOR_B.in2) == PWM_MAX_COUNT


@pytest.mark.parametrize("count", [4096, -4096])
def test_out_of_range_drive_is_rejected_without_writing(count):
    bus, _, motor = make_motor(MOTOR_A)
    with pytest.raises(ValueError):
        motor.drive(count)
    assert bus.writes == []
