"""MotorOutput adapter for one TB6612FNG H-bridge channel driven through the PCA9685.

Signed drive, short brake, and the reverse-polarity flag are borrowed from
https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT).
"""

from typing import NamedTuple

from jack.adapters.hardware.pca9685 import Pca9685
from jack.show.motion.ramp import PWM_MAX_COUNT
from jack.support.attempt_all import attempt_all

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
MOTOR_CHANNELS: dict[str, MotorChannels] = {"A": MOTOR_A, "B": MOTOR_B}


class Tb6612Motor:
    """One DC motor on the HAT. `reverse=True` flips direction for a motor wired with swapped leads."""

    def __init__(self, chip: Pca9685, channels: MotorChannels, reverse: bool = False):
        self._chip = chip
        self._channels = channels
        self._reverse = reverse
        # Direction the IN pins currently hold; None when unknown or braked.
        self._forward: bool | None = None

    def drive(self, count: int) -> None:
        """Run at duty `abs(count)`, forward for count >= 0 and backward for count < 0.

        Direction pins are rewritten only when the direction changes, which keeps
        each ramp step to a single four-register duty write. On a change the duty
        is zeroed first, so the old duty never briefly drives the new direction.
        """
        if abs(count) > PWM_MAX_COUNT:
            raise ValueError(f"drive count must be between -{PWM_MAX_COUNT} and {PWM_MAX_COUNT}, got {count}")
        forward = count >= 0
        if forward != self._forward:
            self._set_duty(0)
            self._set_direction(forward=forward != self._reverse)
            self._forward = forward
        self._set_duty(abs(count))

    def stop(self) -> None:
        """Short brake: zero the duty, then drive IN1 and IN2 high so the TB6612FNG shorts the motor leads."""
        self._release(HIGH)

    def coast(self) -> None:
        """Coast: zero the duty, then drive IN1 and IN2 low so the TB6612FNG leaves the motor leads open."""
        self._release(LOW)

    def _release(self, level: int) -> None:
        """Zero the duty and set both inputs to `level`.

        Each write is attempted even if an earlier one raises, so a transient I2C
        error zeroing the duty doesn't skip the input pins.
        """
        self._forward = None
        attempt_all(
            [
                lambda: self._set_duty(0),
                lambda: self._chip.set_off_count(self._channels.in1, level),
                lambda: self._chip.set_off_count(self._channels.in2, level),
            ]
        )

    def _set_duty(self, count: int) -> None:
        self._chip.set_off_count(self._channels.pwm, count)

    def _set_direction(self, forward: bool) -> None:
        # Waveshare's "forward" is IN1 low, IN2 high.
        if forward:
            self._set_inputs(LOW, HIGH)
        else:
            self._set_inputs(HIGH, LOW)

    def _set_inputs(self, in1: int, in2: int) -> None:
        self._chip.set_off_count(self._channels.in1, in1)
        self._chip.set_off_count(self._channels.in2, in2)
