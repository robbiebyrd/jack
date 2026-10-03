"""Each motor's drive limits and named poses, from poses.toml plus an optional override file.

The repo's poses.toml holds the defaults; /etc/jack/poses.toml on the Pi may override any key or
pose. Every motor is validated at load, so a bad file stops the app before anything moves.
See "Poses and per-motor settings" in SPEC.md.
"""

import itertools
import math
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path

from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.audio.pcm import TICK_S
from jack.show.motion.ramp import whole_steps

REST_MODES = ("brake", "coast")
_REQUIRED = ("calibrated", "min_v", "max_v", "sign", "slew_v_per_s", "holds", "rest")
_OPTIONAL = ("rest_pulse_v", "rest_pulse_s", "poses")


@dataclass(frozen=True)
class Pose:
    volts: float
    seconds: float


@dataclass(frozen=True)
class Hold:
    """A measured safe drive time: driving at `volts` for longer than `seconds` risks the motor."""

    volts: float
    seconds: float


@dataclass(frozen=True)
class MotorProfile:
    calibrated: bool
    min_v: float
    max_v: float
    sign: int
    slew_v_per_s: float
    holds: tuple[Hold, ...]
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

    def hold_s(self, volts: float) -> float:
        """Safe drive time at `volts`, from the hold points on volts' side of 0.

        Interpolated linearly on |volts| between points; below the lowest point, that point's hold.
        Raises ValueError for 0 V or for volts beyond every point on their side (an unmeasured hold).
        """
        side = sorted((abs(hold.volts), hold.seconds) for hold in self.holds if (hold.volts > 0) == (volts > 0))
        magnitude = abs(volts)
        if volts == 0 or not side or magnitude > side[-1][0]:
            raise ValueError(f"no hold measured at or above {volts:+g} V")
        if magnitude <= side[0][0]:
            return side[0][1]
        for (low_v, low_s), (high_v, high_s) in itertools.pairwise(side):
            if magnitude <= high_v:
                return low_s + (high_s - low_s) * (magnitude - low_v) / (high_v - low_v)
        raise AssertionError("unreachable: magnitude is within the side's range")

    @property
    def rest_pulse_ticks(self) -> int:
        """How many 20 ms ticks the rest pulse lasts; 0 when the motor has none."""
        return whole_steps(self.rest_pulse_s, TICK_S) if self.rest_pulse_s > 0 else 0


def load_profiles(paths: Sequence[Path], supply_volts: float) -> dict[str, MotorProfile]:
    """Merge the files in order (later keys and poses replace earlier ones) and validate every motor.

    The first file must exist; later ones are skipped when absent. Raises ValueError for anything
    invalid, starting with the file that last set the offending key or pose, or, for a problem with
    no single key (a missing key, min_v against max_v), every file that set that motor.
    """
    merged: dict[str, dict] = {}
    origins: dict[str, dict] = {}
    files: dict[str, list[Path]] = {}
    for index, path in enumerate(paths):
        if index > 0 and not path.exists():
            continue
        for name, table in _read(path).items():
            if name not in MOTOR_NAMES:
                raise ValueError(f"{path}: unknown motor [{name}]; expected one of {', '.join(MOTOR_NAMES)}")
            if not isinstance(table, dict):
                raise ValueError(f"{path}: {name} must be a [{name}] table")
            if "poses" in table and not isinstance(table["poses"], dict):
                raise ValueError(f"{path}: [{name}] poses must be a table of poses")
            _merge(merged.setdefault(name, {}), origins.setdefault(name, {}), table, path)
            files.setdefault(name, []).append(path)
    missing = [name for name in MOTOR_NAMES if name not in merged]
    if missing:
        raise ValueError(f"no settings for {', '.join(missing)} in {', '.join(str(p) for p in paths)}")
    return {name: _profile(name, merged[name], origins[name], files[name], supply_volts) for name in MOTOR_NAMES}


def _read(path: Path) -> dict:
    with path.open("rb") as file:
        try:
            return tomllib.load(file)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{path}: {error}") from error


def _merge(into: dict, origins: dict, table: dict, path: Path) -> None:
    """Fold `table` into `into`, recording in `origins` which file last set each key and pose."""
    for key, value in table.items():
        if key == "poses":
            into.setdefault("poses", {}).update(value)
            origins.update({("poses", pose): path for pose in value})
        else:
            into[key] = value
            origins[key] = path


@contextmanager
def _blamed_on(path_text: str) -> Iterator[None]:
    """Prefix any ValueError raised inside with the file(s) the bad setting came from."""
    try:
        yield
    except ValueError as error:
        raise ValueError(f"{path_text}: {error}") from error


def _in_file(origins: dict, key: object) -> AbstractContextManager[None]:
    return _blamed_on(str(origins[key]))


def _profile(name: str, table: dict, origins: dict, files: list[Path], supply_volts: float) -> MotorProfile:
    where = f"[{name}]"

    def all_files() -> AbstractContextManager[None]:
        """Blames every file that set this motor; a fresh one per use, since each can be entered only once."""
        return _blamed_on(", ".join(str(path) for path in files))

    if "max_hold_s" in table:
        with _in_file(origins, "max_hold_s"):
            raise ValueError(
                f"{where}: max_hold_s is replaced by holds = [{{ volts = <V>, seconds = <s> }}, ...] (SPEC.md)"
            )
    unknown = sorted(set(table) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        with _in_file(origins, unknown[0]):
            raise ValueError(f"{where}: unknown key(s) {', '.join(unknown)}")
    missing = [key for key in _REQUIRED if key not in table]
    if missing:
        with all_files():
            raise ValueError(f"{where}: missing {', '.join(missing)}")
    with _in_file(origins, "calibrated"):
        if not isinstance(table["calibrated"], bool):
            raise ValueError(f"{where}: calibrated must be true or false")
    with _in_file(origins, "min_v"):
        min_v = _number(where, table, "min_v")
    with _in_file(origins, "max_v"):
        max_v = _number(where, table, "max_v")
        _within_supply(where, "max_v", max_v, supply_volts)
    with all_files():
        if not 0 < min_v <= max_v:
            raise ValueError(f"{where}: need 0 < min_v <= max_v, got min_v {min_v}, max_v {max_v}")
    with _in_file(origins, "sign"):
        if table["sign"] not in (1, -1) or isinstance(table["sign"], bool):
            raise ValueError(f"{where}: sign must be 1 or -1, got {table['sign']!r}")
    with _in_file(origins, "slew_v_per_s"):
        slew = _number(where, table, "slew_v_per_s")
        if slew <= 0:
            raise ValueError(f"{where}: slew_v_per_s must be positive, got {slew}")
    with _in_file(origins, "holds"):
        holds = _holds(where, table["holds"], supply_volts)
    with _in_file(origins, "rest"):
        if table["rest"] not in REST_MODES:
            raise ValueError(f"{where}: rest must be one of {', '.join(REST_MODES)}, got {table['rest']!r}")
    rest_pulse_v = rest_pulse_s = 0.0
    if "rest_pulse_v" in table:
        with _in_file(origins, "rest_pulse_v"):
            rest_pulse_v = _number(where, table, "rest_pulse_v")
            _within_supply(where, "rest_pulse_v", rest_pulse_v, supply_volts)
    if "rest_pulse_s" in table:
        with _in_file(origins, "rest_pulse_s"):
            rest_pulse_s = _number(where, table, "rest_pulse_s")
            if rest_pulse_s < 0:
                raise ValueError(f"{where}: rest_pulse_s must not be negative, got {rest_pulse_s}")
            if rest_pulse_s > 0:
                _ticks(where, "rest_pulse_s", rest_pulse_s)
    poses = {}
    for pose, spec in table.get("poses", {}).items():
        with _in_file(origins, ("poses", pose)):
            poses[pose] = _pose(f"{where} pose {pose}", spec, supply_volts)
    profile = MotorProfile(
        calibrated=table["calibrated"], min_v=min_v, max_v=max_v, sign=int(table["sign"]), slew_v_per_s=slew,
        holds=holds, rest=table["rest"], rest_pulse_v=rest_pulse_v,
        rest_pulse_s=rest_pulse_s, poses=poses,
    )
    with all_files():
        _check_holds(where, profile, motor_spec(name).two_sided)
    return profile


def _pose(where: str, spec: object, supply_volts: float) -> Pose:
    if not isinstance(spec, dict) or set(spec) != {"volts", "seconds"}:
        raise ValueError(f"{where}: must be {{ volts = <V>, seconds = <s> }}, got {spec!r}")
    volts = _number(where, spec, "volts")
    _within_supply(where, "volts", volts, supply_volts)
    seconds = _number(where, spec, "seconds")
    if seconds <= 0:
        raise ValueError(f"{where}: seconds must be positive, got {seconds}")
    return Pose(volts, seconds)


def _holds(where: str, value: object, supply_volts: float) -> tuple[Hold, ...]:
    """The hold points, sorted by volts; each nonzero, within the supply, positive seconds, volts unique."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: holds must be a non-empty list of {{ volts = <V>, seconds = <s> }}, got {value!r}")
    holds = []
    for spec in value:
        if not isinstance(spec, dict) or set(spec) != {"volts", "seconds"}:
            raise ValueError(f"{where}: each of holds must be {{ volts = <V>, seconds = <s> }}, got {spec!r}")
        volts = _number(f"{where} holds", spec, "volts")
        _within_supply(f"{where} holds", "volts", volts, supply_volts)
        seconds = _number(f"{where} holds", spec, "seconds")
        if volts == 0 or seconds <= 0:
            raise ValueError(f"{where}: holds need nonzero volts and positive seconds, got {spec!r}")
        holds.append(Hold(volts, seconds))
    volts_seen = [hold.volts for hold in holds]
    duplicates = sorted({volts for volts in volts_seen if volts_seen.count(volts) > 1})
    if duplicates:
        raise ValueError(f"{where}: holds list {', '.join(f'{volts:+g} V' for volts in duplicates)} more than once")
    return tuple(sorted(holds, key=lambda hold: hold.volts))


def _check_holds(where: str, profile: MotorProfile, two_sided: bool) -> None:
    """Refuse anything that could drive the motor on an unmeasured hold, or longer than its hold."""
    driven = [profile.sign * profile.max_v]
    if two_sided:
        driven.append(-profile.sign * profile.max_v)
    for volts in driven:
        _hold_at(f"{where} max_v", profile, volts)
    for name, pose in profile.poses.items():
        _fits_hold(f"{where} pose {name}", profile, pose.volts, pose.seconds)
    if profile.rest_pulse_s > 0 and profile.rest_pulse_v != 0:
        _fits_hold(f"{where} rest_pulse", profile, profile.rest_pulse_v, profile.rest_pulse_s)


def _hold_at(where: str, profile: MotorProfile, volts: float) -> float:
    try:
        return profile.hold_s(volts)
    except ValueError as error:
        raise ValueError(f"{where}: drives {volts:+g} V, but {error}; add a holds point") from error


def _fits_hold(where: str, profile: MotorProfile, volts: float, seconds: float) -> None:
    hold = _hold_at(where, profile, volts)
    if seconds > hold:
        raise ValueError(f"{where}: {seconds:g} s is longer than the {hold:g} s hold at {volts:+g} V")


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
