"""Each motor's drive limits and named poses, from poses.toml plus an optional override file.

The repo's poses.toml holds the defaults; /etc/jack/poses.toml on the Pi may override any key or
pose. Every motor is validated at load, so a bad file stops the app before anything moves.
See "Poses and per-motor settings" in SPEC.md.
"""

import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from motor_test.motors import MOTOR_NAMES
from motor_test.pcm import TICK_S
from motor_test.ramp import whole_steps

REST_MODES = ("brake", "coast")
_REQUIRED = ("calibrated", "min_v", "max_v", "sign", "slew_v_per_s", "max_hold_s", "rest")
_OPTIONAL = ("rest_pulse_v", "rest_pulse_s", "poses")


@dataclass(frozen=True)
class Pose:
    volts: float
    seconds: float


@dataclass(frozen=True)
class MotorProfile:
    calibrated: bool
    min_v: float
    max_v: float
    sign: int
    slew_v_per_s: float
    max_hold_s: float
    rest: str
    rest_pulse_v: float
    rest_pulse_s: float
    poses: Mapping[str, Pose]

    def volts_for(self, value: float) -> float:
        """Signed volts for a command value: 0 is rest; otherwise min_v..max_v by |value|, in `sign`'s direction."""
        if value == 0:
            return 0.0
        magnitude = self.min_v + (self.max_v - self.min_v) * min(1.0, abs(value))
        return math.copysign(magnitude, value) * self.sign


def load_profiles(paths: Sequence[Path], supply_volts: float) -> dict[str, MotorProfile]:
    """Merge the files in order (later keys and poses replace earlier ones) and validate every motor.

    The first file must exist; later ones are skipped when absent. Raises ValueError naming the
    file, motor and key for anything invalid.
    """
    merged: dict[str, dict] = {}
    for index, path in enumerate(paths):
        if index > 0 and not path.exists():
            continue
        for name, table in _read(path).items():
            if name not in MOTOR_NAMES:
                raise ValueError(f"{path}: unknown motor [{name}]; expected one of {', '.join(MOTOR_NAMES)}")
            if not isinstance(table, dict):
                raise ValueError(f"{path}: {name} must be a [{name}] table")
            _merge(merged.setdefault(name, {}), table)
    missing = [name for name in MOTOR_NAMES if name not in merged]
    if missing:
        raise ValueError(f"no settings for {', '.join(missing)} in {', '.join(str(p) for p in paths)}")
    return {name: _profile(name, merged[name], supply_volts) for name in MOTOR_NAMES}


def _read(path: Path) -> dict:
    with path.open("rb") as file:
        try:
            return tomllib.load(file)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{path}: {error}") from error


def _merge(into: dict, table: dict) -> None:
    for key, value in table.items():
        if key == "poses" and isinstance(value, dict):
            into.setdefault("poses", {}).update(value)
        else:
            into[key] = value


def _profile(name: str, table: dict, supply_volts: float) -> MotorProfile:
    where = f"[{name}]"
    unknown = set(table) - set(_REQUIRED) - set(_OPTIONAL)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    missing = [key for key in _REQUIRED if key not in table]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    if not isinstance(table["calibrated"], bool):
        raise ValueError(f"{where}: calibrated must be true or false")
    min_v = _number(where, table, "min_v")
    max_v = _number(where, table, "max_v")
    if not 0 < min_v <= max_v:
        raise ValueError(f"{where}: need 0 < min_v <= max_v, got min_v {min_v}, max_v {max_v}")
    _within_supply(where, "max_v", max_v, supply_volts)
    if table["sign"] not in (1, -1) or isinstance(table["sign"], bool):
        raise ValueError(f"{where}: sign must be 1 or -1, got {table['sign']!r}")
    slew = _number(where, table, "slew_v_per_s")
    if slew <= 0:
        raise ValueError(f"{where}: slew_v_per_s must be positive, got {slew}")
    max_hold_s = _ticks(where, "max_hold_s", _number(where, table, "max_hold_s"))
    if table["rest"] not in REST_MODES:
        raise ValueError(f"{where}: rest must be one of {', '.join(REST_MODES)}, got {table['rest']!r}")
    rest_pulse_v = _number(where, table, "rest_pulse_v", default=0.0)
    _within_supply(where, "rest_pulse_v", rest_pulse_v, supply_volts)
    rest_pulse_s = _number(where, table, "rest_pulse_s", default=0.0)
    if rest_pulse_s < 0:
        raise ValueError(f"{where}: rest_pulse_s must not be negative, got {rest_pulse_s}")
    if rest_pulse_s > 0:
        _ticks(where, "rest_pulse_s", rest_pulse_s)
    poses = {pose: _pose(f"{where} pose {pose}", spec, supply_volts) for pose, spec in table.get("poses", {}).items()}
    return MotorProfile(
        calibrated=table["calibrated"], min_v=min_v, max_v=max_v, sign=table["sign"], slew_v_per_s=slew,
        max_hold_s=max_hold_s, rest=table["rest"], rest_pulse_v=rest_pulse_v, rest_pulse_s=rest_pulse_s,
        poses=poses,
    )


def _pose(where: str, spec: object, supply_volts: float) -> Pose:
    if not isinstance(spec, dict) or set(spec) != {"volts", "seconds"}:
        raise ValueError(f"{where}: must be {{ volts = <V>, seconds = <s> }}, got {spec!r}")
    volts = _number(where, spec, "volts")
    _within_supply(where, "volts", volts, supply_volts)
    seconds = _number(where, spec, "seconds")
    if seconds <= 0:
        raise ValueError(f"{where}: seconds must be positive, got {seconds}")
    return Pose(volts, seconds)


def _number(where: str, table: dict, key: str, default: float | None = None) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{where}: {key} must be a finite number, got {value!r}")
    return float(value)


def _within_supply(where: str, key: str, volts: float, supply_volts: float) -> None:
    if abs(volts) > supply_volts:
        raise ValueError(f"{where}: {key} {volts} V exceeds the {supply_volts} V supply")


def _ticks(where: str, key: str, seconds: float) -> float:
    try:
        whole_steps(seconds, TICK_S)
    except ValueError as error:
        raise ValueError(f"{where}: {key}: {error}") from error
    return seconds
