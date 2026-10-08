"""One set of routes for OSC and HTTP, so both behave identically.

A route is an OSC address below /jack or an HTTP path: /<motor> value; /<motor>/pose name [seconds];
/<motor>/rest; /rest; /mouth/mode live|show. Its parameters arrive by name (an HTTP JSON body) or by
position (OSC arguments, named by `argument_names`). See "OSC" and "HTTP" in SPEC.md.
"""

import math
from collections.abc import Mapping

from jack.show.control.commands import Command, CommandError, RestCommand, SetMouthMode, SetValue, StartPose
from jack.show.control.control_board import MOUTH_MODES
from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.motion.poses import MotorProfile
from jack.support.clip import clip


def command_for(route: str, params: Mapping[str, object], profiles: Mapping[str, MotorProfile]) -> Command:
    """The command a route and its named parameters mean, validated and clamped; CommandError says why not."""
    parts = route_parts(route)
    if parts == ["rest"]:
        _only(params, set())
        return RestCommand(None)
    if parts == ["mouth", "mode"]:
        _only(params, {"mode"})
        mode = params.get("mode")
        if mode not in MOUTH_MODES:
            raise CommandError(f"mode must be one of {', '.join(MOUTH_MODES)}, got {mode!r}")
        return SetMouthMode(mode)
    if not parts or parts[0] not in MOTOR_NAMES:
        raise CommandError(f"unknown motor or route {route!r}; motors: {', '.join(MOTOR_NAMES)}", 404)
    motor, rest_of_route = parts[0], parts[1:]
    if rest_of_route == []:
        _only(params, {"value"})
        requested = _number(params, "value")
        low = -1.0 if motor_spec(motor).two_sided else 0.0
        return SetValue(motor, min(1.0, max(low, requested)), requested)
    if rest_of_route == ["pose"]:
        _only(params, {"name", "seconds"})
        name = params.get("name")
        if not isinstance(name, str):
            raise CommandError("pose needs a name")
        check_pose(motor, name, profiles)
        seconds = None if params.get("seconds") is None else _number(params, "seconds")
        if seconds is not None and seconds <= 0:
            raise CommandError(f"seconds must be positive, got {seconds}")
        return StartPose(motor, name, seconds)
    if rest_of_route == ["rest"]:
        _only(params, set())
        return RestCommand(motor)
    raise CommandError(f"unknown route {route!r}", 404)


def route_parts(route: str) -> list[str]:
    return [part for part in route.split("/") if part]


def argument_names(parts: list[str]) -> list[str]:
    """OSC arguments are positional; these are their names, matching the HTTP JSON fields."""
    if parts == ["mouth", "mode"]:
        return ["mode"]
    if len(parts) == 2 and parts[1] == "pose":
        return ["name", "seconds"]
    if len(parts) == 1 and parts[0] in MOTOR_NAMES:
        return ["value"]
    return []


def check_pose(motor: str, name: str, profiles: Mapping[str, MotorProfile]) -> None:
    if name not in profiles[motor].poses:
        raise CommandError(f"unknown pose {name!r} for {motor}; poses: {', '.join(sorted(profiles[motor].poses))}", 404)


def _only(params: Mapping[str, object], allowed: set[str]) -> None:
    unexpected = set(params) - allowed
    if unexpected:
        raise CommandError(f"unexpected {', '.join(sorted(unexpected))}")


def _number(params: Mapping[str, object], key: str) -> float:
    if key not in params:
        raise CommandError(f"{key} is required")
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError(f"{key} must be a number, got {value!r}")
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        raise CommandError(f"{key} must be a finite number, got {clip(value)}")
    return number
