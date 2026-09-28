"""MotorOutput adapter for one TB6612FNG channel on the Waveshare Motor Driver HAT.

The register sequence follows Waveshare's PCA9685.py sample driver, but duty is
written as a raw 12-bit count instead of Waveshare's percentage (which scales by
40 and never reaches the full range).
"""

import math
import time
from typing import Protocol

from motor_test.ramp import PWM_MAX_COUNT, PWM_RESOLUTION

MODE1 = 0x00
PRESCALE = 0xFE
LED0_ON_L = 0x06
MODE1_SLEEP = 0x10
MODE1_RESTART = 0x80
OSCILLATOR_HZ = 25_000_000


class I2CBus(Protocol):
    """The subset of smbus2.SMBus this adapter uses."""

    def write_byte_data(self, i2c_addr: int, register: int, value: int) -> None: ...

    def read_byte_data(self, i2c_addr: int, register: int) -> int: ...


class Pca9685Motor:
    """Drives one H-bridge channel: a PWM speed pin plus two direction pins."""

    def __init__(
        self,
        bus: I2CBus,
        address: int,
        pwm_channel: int,
        in1_channel: int,
        in2_channel: int,
        pwm_freq_hz: float,
    ):
        self._bus = bus
        self._address = address
        self._pwm_channel = pwm_channel
        self._in1_channel = in1_channel
        self._in2_channel = in2_channel
        self._write(MODE1, 0x00)
        self._set_frequency(pwm_freq_hz)

    def set_forward(self) -> None:
        self._set_off_count(self._in1_channel, 0)
        self._set_off_count(self._in2_channel, PWM_MAX_COUNT)

    def set_duty(self, count: int) -> None:
        if not 0 <= count <= PWM_MAX_COUNT:
            raise ValueError(f"duty count must be between 0 and {PWM_MAX_COUNT}, got {count}")
        self._set_off_count(self._pwm_channel, count)

    def stop(self) -> None:
        self.set_duty(0)

    def _set_frequency(self, freq_hz: float) -> None:
        prescale = math.floor(OSCILLATOR_HZ / PWM_RESOLUTION / freq_hz - 1 + 0.5)
        mode = self._bus.read_byte_data(self._address, MODE1)
        self._write(MODE1, (mode & 0x7F) | MODE1_SLEEP)
        self._write(PRESCALE, prescale)
        self._write(MODE1, mode)
        time.sleep(0.005)  # same settle time Waveshare's driver waits before restart
        self._write(MODE1, mode | MODE1_RESTART)

    def _set_off_count(self, channel: int, off_count: int) -> None:
        base = LED0_ON_L + 4 * channel
        self._write(base, 0)
        self._write(base + 1, 0)
        self._write(base + 2, off_count & 0xFF)
        self._write(base + 3, off_count >> 8)

    def _write(self, register: int, value: int) -> None:
        self._bus.write_byte_data(self._address, register, value)
