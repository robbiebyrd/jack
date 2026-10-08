"""Jack's state as one snapshot: what HTTP /status and the OSC feedback report (SPEC.md "HTTP", "OSC replies")."""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ValueCommand:
    """A continuous value in force, and how long ago it arrived."""

    value: float
    age_s: float


@dataclass(frozen=True)
class PoseCommand:
    """A pose playing, and how long it has left."""

    pose: str
    seconds_left: float


@dataclass(frozen=True)
class MotorStatus:
    command: ValueCommand | PoseCommand | None
    volts: float
    max_hold_tripped: bool
    calibrated: bool
    two_sided: bool
    poses: tuple[str, ...]

    @property
    def fader_value(self) -> float:
        """What a fader mirroring this motor shows: the value command, 0 at rest or during a pose."""
        return self.command.value if isinstance(self.command, ValueCommand) else 0.0


@dataclass(frozen=True)
class Status:
    mouth_mode: str
    mumble_connected: bool
    motors: Mapping[str, MotorStatus]

    def to_json(self) -> dict[str, object]:
        """The /status document: the same fields as JSON-ready dicts (a command is a dict or null)."""
        return dataclasses.asdict(self)
