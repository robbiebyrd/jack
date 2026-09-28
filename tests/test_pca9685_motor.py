import pytest

from motor_test.pca9685_motor import Pca9685Motor

ADDRESS = 0x40


class RecordingBus:
    """In-memory PCA9685 register file that records every byte write."""

    def __init__(self):
        self.registers = {}
        self.writes = []

    def write_byte_data(self, i2c_addr, register, value):
        self.writes.append((i2c_addr, register, value))
        self.registers[register] = value

    def read_byte_data(self, i2c_addr, register):
        return self.registers.get(register, 0)


def motor_b(bus):
    return Pca9685Motor(bus, ADDRESS, pwm_channel=5, in1_channel=3, in2_channel=4, pwm_freq_hz=50)


def test_init_resets_mode_and_sets_50hz_prescale_like_waveshare():
    bus = RecordingBus()
    motor_b(bus)
    assert bus.writes == [
        (ADDRESS, 0x00, 0x00),  # MODE1 reset
        (ADDRESS, 0x00, 0x10),  # sleep to change prescale
        (ADDRESS, 0xFE, 121),  # prescale for 50 Hz
        (ADDRESS, 0x00, 0x00),  # wake
        (ADDRESS, 0x00, 0x80),  # restart
    ]


def test_set_duty_writes_off_count_to_pwmb_channel():
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    motor.set_duty(2048)
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x08),
    ]


def test_set_forward_drives_bin1_low_and_bin2_high():
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    motor.set_forward()
    assert bus.writes == [
        (ADDRESS, 0x12, 0x00),
        (ADDRESS, 0x13, 0x00),
        (ADDRESS, 0x14, 0x00),
        (ADDRESS, 0x15, 0x00),
        (ADDRESS, 0x16, 0x00),
        (ADDRESS, 0x17, 0x00),
        (ADDRESS, 0x18, 0xFF),
        (ADDRESS, 0x19, 0x0F),
    ]


def test_stop_sets_pwmb_duty_to_zero():
    bus = RecordingBus()
    motor = motor_b(bus)
    motor.set_duty(2048)
    bus.writes.clear()
    motor.stop()
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x00),
    ]


@pytest.mark.parametrize("count", [-1, 4096])
def test_out_of_range_duty_is_rejected_without_writing(count):
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    with pytest.raises(ValueError):
        motor.set_duty(count)
    assert bus.writes == []
