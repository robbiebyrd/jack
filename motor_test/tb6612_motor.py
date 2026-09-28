"""MotorOutput adapter for one TB6612FNG H-bridge channel driven through the PCA9685.

Short brake, backward, and the reverse-polarity flag are borrowed from
https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT).
"""

from typing import NamedTuple

from motor_test.attempt_all import attempt_all
from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT

LOW = 0
HIGH = PWM_MAX_COUNT


class MotorChannels(NamedTuple):
    """PCA9685 channels wired to one TB6612FNG channel's PWM, IN1 and IN2 pins."""

    pwm: int
    in1: int
    in2: int


# Channel mapping from Waveshare's Motor Driver HAT sample code.
MOTOR_A = MotorChannels(pwm=0, in1=1, in2=2)
MOTOR_B = MotorChannels(pwm=5, in1=3, in2=4)


class Tb6612Motor:
    """One DC motor on the HAT. `reverse=True` flips direction for a motor wired with swapped leads."""

    def __init__(self, chip: Pca9685, channels: MotorChannels, reverse: bool = False):
        self._chip = chip
        self._channels = channels
        self._reverse = reverse

    def set_forward(self) -> None:
        self._set_direction(forward=not self._reverse)

    def set_backward(self) -> None:
        self._set_direction(forward=self._reverse)

    def set_duty(self, count: int) -> None:
        self._chip.set_off_count(self._channels.pwm, count)

    def stop(self) -> None:
        """Short brake: zero the duty, then drive IN1 and IN2 high so the TB6612FNG shorts the motor leads.

        Each write is attempted even if an earlier one raises, so a transient I2C
        error zeroing the duty doesn't skip the brake pins.
        """
        attempt_all(
            [
                lambda: self.set_duty(0),
                lambda: self._chip.set_off_count(self._channels.in1, HIGH),
                lambda: self._chip.set_off_count(self._channels.in2, HIGH),
            ]
        )

    def _set_direction(self, forward: bool) -> None:
        # Waveshare's "forward" is IN1 low, IN2 high.
        if forward:
            self._set_inputs(LOW, HIGH)
        else:
            self._set_inputs(HIGH, LOW)

    def _set_inputs(self, in1: int, in2: int) -> None:
        self._chip.set_off_count(self._channels.in1, in1)
        self._chip.set_off_count(self._channels.in2, in2)
