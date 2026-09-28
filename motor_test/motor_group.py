"""Drives several motors as one MotorOutput."""

from collections.abc import Sequence

from motor_test.attempt_all import attempt_all
from motor_test.ports import MotorOutput


class MotorGroup:
    """Sends every command to each motor, in order."""

    def __init__(self, motors: Sequence[MotorOutput]):
        if not motors:
            raise ValueError("MotorGroup needs at least one motor")
        self._motors = list(motors)

    def set_forward(self) -> None:
        for motor in self._motors:
            motor.set_forward()

    def set_duty(self, count: int) -> None:
        for motor in self._motors:
            motor.set_duty(count)

    def stop(self) -> None:
        """Stop every motor, even if stopping one fails, then re-raise the first failure."""
        attempt_all(motor.stop for motor in self._motors)
