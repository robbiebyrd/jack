"""Ports the smoke test depends on."""

from typing import Protocol


class MotorOutput(Protocol):
    """One DC motor output driven by a PWM duty count."""

    def set_forward(self) -> None: ...

    def set_duty(self, count: int) -> None: ...

    def stop(self) -> None: ...
