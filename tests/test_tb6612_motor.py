import pytest

from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, MotorChannels, Tb6612Motor
from tests.fakes import RecordingBus

ADDRESS = 0x40


def make_motor(channels, reverse=False):
    bus = RecordingBus()
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    bus.writes.clear()
    return bus, chip, Tb6612Motor(chip, channels, reverse=reverse)


def off_count(bus, channel):
    """Decode the 12-bit OFF count the chip holds for `channel`."""
    base = 0x06 + 4 * channel
    return bus.registers[base + 2] | (bus.registers[base + 3] << 8)


def test_channel_mapping_matches_waveshare_sample_code():
    assert MOTOR_A == MotorChannels(pwm=0, in1=1, in2=2)
    assert MOTOR_B == MotorChannels(pwm=5, in1=3, in2=4)


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_forward_drives_in1_low_and_in2_high(channels):
    bus, _, motor = make_motor(channels)
    motor.set_forward()
    assert off_count(bus, channels.in1) == 0
    assert off_count(bus, channels.in2) == PWM_MAX_COUNT


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_backward_drives_in1_high_and_in2_low(channels):
    bus, _, motor = make_motor(channels)
    motor.set_backward()
    assert off_count(bus, channels.in1) == PWM_MAX_COUNT
    assert off_count(bus, channels.in2) == 0


def test_reverse_flag_swaps_forward_and_backward():
    bus, _, motor = make_motor(MOTOR_B, reverse=True)
    motor.set_forward()
    assert (off_count(bus, 3), off_count(bus, 4)) == (PWM_MAX_COUNT, 0)
    motor.set_backward()
    assert (off_count(bus, 3), off_count(bus, 4)) == (0, PWM_MAX_COUNT)


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_set_duty_drives_the_motors_pwm_channel(channels):
    bus, _, motor = make_motor(channels)
    motor.set_duty(2048)
    assert off_count(bus, channels.pwm) == 2048


def test_two_motors_on_one_chip_keep_separate_channels():
    bus, chip, motor_a = make_motor(MOTOR_A)
    motor_b = Tb6612Motor(chip, MOTOR_B)
    motor_a.set_duty(2048)
    motor_b.set_duty(1000)
    assert off_count(bus, 0) == 2048
    assert off_count(bus, 5) == 1000


def test_stop_zeroes_duty_before_short_braking():
    bus, _, motor = make_motor(MOTOR_B)
    motor.set_forward()
    motor.set_duty(2048)
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


def test_out_of_range_duty_is_rejected():
    bus, _, motor = make_motor(MOTOR_A)
    with pytest.raises(ValueError):
        motor.set_duty(4096)
    assert bus.writes == []
