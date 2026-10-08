"""The latest show-control command for each motor, shared by the network threads and the talk loop.

OSC and HTTP threads write; the talk loop reads each motor's target once per tick and reports
back what it drove, for /status. A value holds until `timeout_s` passes without a new one
(dead-man); a pose holds for its duration. See "Command board" in SPEC.md.
"""

import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from jack.show.control.status import MotorStatus, PoseCommand, Status, ValueCommand
from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.motion.poses import MotorProfile

MOUTH_MODES = ("live", "show")


@dataclass(frozen=True)
class _Value:
    value: float
    received_at: float


@dataclass(frozen=True)
class _Pose:
    name: str
    started_at: float
    seconds: float


class ControlBoard:
    def __init__(
        self, profiles: Mapping[str, MotorProfile], timeout_s: float, mouth_mode: str, clock: Callable[[], float]
    ):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError(f"control timeout must be a positive number of seconds, got {timeout_s}")
        _check_mode(mouth_mode)
        self._profiles = profiles
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._mouth_mode = mouth_mode
        self._commands: dict[str, _Value | _Pose] = {}
        self._reports: dict[str, tuple[float, bool]] = dict.fromkeys(MOTOR_NAMES, (0.0, False))

    @property
    def mouth_mode(self) -> str:
        with self._lock:
            return self._mouth_mode

    def set_mouth_mode(self, mode: str) -> None:
        """Switch the mouth's source; a real switch drops the mouth's command so a stale one can't resume later."""
        _check_mode(mode)
        with self._lock:
            if mode != self._mouth_mode:
                self._commands.pop("mouth", None)
            self._mouth_mode = mode

    def set_value(self, motor: str, value: float) -> None:
        with self._lock:
            self._commands[motor] = _Value(value, self._clock())

    def start_pose(self, motor: str, name: str, seconds: float | None = None) -> None:
        pose = self._profiles[motor].poses.get(name)
        if pose is None:
            raise ValueError(f"unknown pose {name!r} for {motor}")
        with self._lock:
            self._commands[motor] = _Pose(name, self._clock(), pose.seconds if seconds is None else seconds)

    def rest(self, motor: str) -> None:
        with self._lock:
            self._commands.pop(motor, None)

    def rest_all(self) -> None:
        with self._lock:
            self._commands.clear()

    def target_volts(self, motor: str) -> float | None:
        """The volts `motor` is commanded to now, or None when it should rest."""
        with self._lock:
            command = self._current(motor, self._clock())
        if command is None:
            return None
        if isinstance(command, _Value):
            return self._profiles[motor].volts_for(command.value)
        return self._profiles[motor].poses[command.name].volts

    def report(self, motor: str, volts: float, max_hold_tripped: bool) -> None:
        """Record what the talk loop drove, for /status."""
        with self._lock:
            self._reports[motor] = (volts, max_hold_tripped)

    def status(self, mumble_connected: bool) -> Status:
        """A consistent snapshot of every motor's command and report, the mouth mode and the Mumble link."""
        with self._lock:
            now = self._clock()
            motors = {name: self._motor_status(name, now) for name in MOTOR_NAMES}
            return Status(mouth_mode=self._mouth_mode, mumble_connected=mumble_connected, motors=motors)

    def _motor_status(self, name: str, now: float) -> MotorStatus:
        """Caller holds the lock."""
        volts, tripped = self._reports[name]
        profile = self._profiles[name]
        return MotorStatus(
            command=_describe(self._current(name, now), now),
            volts=volts,
            max_hold_tripped=tripped,
            calibrated=profile.calibrated,
            two_sided=motor_spec(name).two_sided,
            poses=tuple(sorted(profile.poses)),
        )

    def _current(self, motor: str, now: float) -> _Value | _Pose | None:
        """The motor's command if still in force, dropping it once expired. Caller holds the lock."""
        command = self._commands.get(motor)
        expired = (isinstance(command, _Value) and now - command.received_at > self._timeout_s) or (
            isinstance(command, _Pose) and now - command.started_at >= command.seconds
        )
        if expired:
            del self._commands[motor]
            return None
        return command


def _check_mode(mode: str) -> None:
    if mode not in MOUTH_MODES:
        raise ValueError(f"mouth mode must be one of {', '.join(MOUTH_MODES)}, got {mode!r}")


def _describe(command: _Value | _Pose | None, now: float) -> ValueCommand | PoseCommand | None:
    if command is None:
        return None
    if isinstance(command, _Value):
        return ValueCommand(command.value, round(now - command.received_at, 3))
    return PoseCommand(command.name, round(command.started_at + command.seconds - now, 3))
