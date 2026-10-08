import pytest

from jack.adapters.hardware.motor_hats import build_motors
from tests.fakes import RecordingBus

PWM_FREQ_HZ = 50


def test_build_motors_puts_each_motor_on_its_hat_and_channel():
    bus = RecordingBus()
    motors = build_motors(bus, PWM_FREQ_HZ)
    assert set(motors) == {"mouth", "hand", "pivot", "elbow"}
    motors["elbow"].drive(100)
    assert (0x41, 0x06 + 4 * 5 + 2, 100) in bus.writes  # HAT 2, channel B's PWM (channel 5) OFF_L


class MissingHatBus(RecordingBus):
    def write_byte_data(self, i2c_addr, register, value):
        if i2c_addr == 0x41:
            raise OSError("[Errno 121] Remote I/O error")
        super().write_byte_data(i2c_addr, register, value)


def test_a_missing_hat_raises_naming_its_address():
    with pytest.raises(OSError, match="0x41"):
        build_motors(MissingHatBus(), PWM_FREQ_HZ)


def test_the_first_hat_is_braked_before_a_missing_second_hat_stops_the_build():
    bus = MissingHatBus()
    with pytest.raises(OSError, match="0x41"):
        build_motors(bus, PWM_FREQ_HZ)

    def off_register_writes(channel, low, high):
        base = 0x06 + 4 * channel
        return {(0x40, base + 2, low), (0x40, base + 3, high)}

    for direction_pin in (1, 2, 3, 4):  # IN1 and IN2 of channels A and B held high: short brake
        assert off_register_writes(direction_pin, 0xFF, 0x0F) <= set(bus.writes)
    for pwm in (0, 5):  # duty zeroed on both channels
        assert off_register_writes(pwm, 0x00, 0x00) <= set(bus.writes)
