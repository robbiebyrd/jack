"""Ports the application layer depends on."""

from typing import Protocol


class MotorOutput(Protocol):
    """One DC motor output driven by a signed PWM duty count."""

    def drive(self, count: int) -> None:
        """Run at duty `abs(count)`: forward for count >= 0, backward for count < 0."""
        ...

    def stop(self) -> None: ...

    def coast(self) -> None:
        """Stop driving and leave the motor free to turn (no braking)."""
        ...


class VoiceSource(Protocol):
    """Where voices come from: a Mumble channel, a WAV file, later pre-recorded clips."""

    def take_frames(self) -> list[bytes]:
        """The next frame from each voice currently sounding; empty when all are quiet."""
        ...


class AudioSink(Protocol):
    """Where the talk loop's audio goes."""

    def write(self, frame: bytes) -> None:
        """Play one frame, blocking while the device's buffer is full; this paces the talk loop."""
        ...

    def close(self) -> None: ...
