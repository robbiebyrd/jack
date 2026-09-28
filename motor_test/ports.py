"""Ports the smoke test depends on."""

from typing import Protocol


class MotorOutput(Protocol):
    """One DC motor output driven by a signed PWM duty count."""

    def drive(self, count: int) -> None:
        """Run at duty `abs(count)`: forward for count >= 0, backward for count < 0."""
        ...

    def stop(self) -> None: ...
