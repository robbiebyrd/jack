"""PCA9685 12-bit PWM controller on the Waveshare Motor Driver HAT.

The register sequence follows Waveshare's PCA9685.py sample driver, but outputs
are written as raw 12-bit counts instead of Waveshare's percentage (which scales
by 40 and never reaches the full range).
"""

import math
import time
from typing import Protocol

from jack.show.motion.ramp import PWM_MAX_COUNT, PWM_RESOLUTION

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


class Pca9685:
    """One PCA9685 chip. Create it once and share it between the motors it drives."""

    def __init__(self, bus: I2CBus, address: int, pwm_freq_hz: float):
        self._bus = bus
        self._address = address
        self._write(MODE1, 0x00)
        self._set_frequency(pwm_freq_hz)

    def set_off_count(self, channel: int, off_count: int) -> None:
        """Hold `channel` high for `off_count` of every 4096 ticks (0 = always low)."""
        if not 0 <= off_count <= PWM_MAX_COUNT:
            raise ValueError(f"off count must be between 0 and {PWM_MAX_COUNT}, got {off_count}")
        base = LED0_ON_L + 4 * channel
        self._write(base, 0)
        self._write(base + 1, 0)
        self._write(base + 2, off_count & 0xFF)
        self._write(base + 3, off_count >> 8)

    def _set_frequency(self, freq_hz: float) -> None:
        prescale = math.floor(OSCILLATOR_HZ / PWM_RESOLUTION / freq_hz - 1 + 0.5)
        mode = self._bus.read_byte_data(self._address, MODE1)
        self._write(MODE1, (mode & 0x7F) | MODE1_SLEEP)
        self._write(PRESCALE, prescale)
        self._write(MODE1, mode)
        time.sleep(0.005)  # same settle time Waveshare's driver waits before restart
        self._write(MODE1, mode | MODE1_RESTART)

    def _write(self, register: int, value: int) -> None:
        self._bus.write_byte_data(self._address, register, value)
