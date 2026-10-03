"""Turns OSC messages and HTTP requests into command-board actions, with one set of rules for both.

Routes (an OSC address below /jack, or an HTTP path): /<motor> value; /<motor>/pose name [seconds];
/<motor>/rest; /rest; /mouth/mode live|show. See "OSC" and "HTTP" in SPEC.md.
OSC alone also answers /ping, /status, and takes /subscribe [port], /unsubscribe [port]; see "OSC replies and feedback".
TouchOSC forms (OSC only; see "TouchOSC support"): ports may be whole-number floats; /rest, /<motor>/rest, /ping, /status
and /<motor>/pose/<name> are buttons (a press or no argument acts, a release of 0 is ignored, NaN or infinity is a 400);
/subscribe and /unsubscribe ignore a release of 0 too, and their ports are 1024-65535; /mouth/mode and
/mouth/mode/show take a number, non-zero for show and 0 for live.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from jack.show.control.control_board import MOUTH_MODES, ControlBoard
from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.control.osc_feedback import MAX_SUBSCRIBERS, Destination, Message, Subscribers, state_messages
from jack.show.motion.poses import MotorProfile
from jack.support.rate_limited_log import RateLimitedLog

OSC_PREFIX = "/jack"
MAX_LOGGED_CHARS = 200


class CommandError(ValueError):
    """A command that can't be carried out; `status` is the HTTP status that says why."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SetValue:
    motor: str
    value: float
    requested: float  # as sent, before clamping


@dataclass(frozen=True)
class StartPose:
    motor: str
    name: str
    seconds: float | None


@dataclass(frozen=True)
class RestCommand:
    motor: str | None  # None rests every motor


@dataclass(frozen=True)
class SetMouthMode:
    mode: str


@dataclass(frozen=True)
class Ping:
    pass


@dataclass(frozen=True)
class StatusRequest:
    pass


@dataclass(frozen=True)
class Subscribe:
    port: int | None  # None means the configured reply port, else the sender's source port


@dataclass(frozen=True)
class Unsubscribe:
    port: int | None


Command = SetValue | StartPose | RestCommand | SetMouthMode
OscQuery = Ping | StatusRequest | Subscribe | Unsubscribe


@dataclass(frozen=True)
class OscContext:
    """What handling an OSC message needs besides the message: the robot, who to reply to, and how."""

    profiles: Mapping[str, MotorProfile]
    board: ControlBoard
    subscribers: Subscribers
    send: Callable[[Destination, list[Message]], None]
    mumble_connected: Callable[[], bool]
    reply_port: int | None
    log: RateLimitedLog


_QUERY_ROUTES = ("/ping", "/status", "/subscribe", "/unsubscribe")


def osc_command(
    address: str, args: Sequence[object], profiles: Mapping[str, MotorProfile],
) -> Command | OscQuery | None:
    """Translate one OSC message; None means a button release, which is ignored."""
    if not address.startswith(OSC_PREFIX + "/"):
        raise CommandError(f"unknown address {address!r}", 404)
    route = address[len(OSC_PREFIX):]
    parts = _parts(route)
    if route == "/mouth/mode/show" or (route == "/mouth/mode" and len(args) == 1 and _is_button_value(args[0])):
        return _mouth_mode_toggle(route, args)
    if _is_button_route(route, parts):
        if not _pressed(args):
            return None
        args = []
        if len(parts) == 3:
            return _pose_button(parts[0], parts[2], profiles)
    if route in _QUERY_ROUTES:
        return None if _is_release(route, args) else _query(route, args)
    names = _argument_names(parts)
    # Build first, so an unknown address is a 404 even when it carries arguments.
    command = _build(route, dict(zip(names, args)), profiles)
    if len(args) > len(names):
        raise CommandError(f"too many arguments for {address}")
    return command


def http_command(path: str, body: Mapping[str, object], profiles: Mapping[str, MotorProfile]) -> Command:
    return _build(path, dict(body), profiles)


def apply(command: Command, board: ControlBoard) -> None:
    """Carry out `command`; a mouth move while the mouth follows the live voice is a 409 conflict."""
    if isinstance(command, SetValue | StartPose) and command.motor == "mouth" and board.mouth_mode == "live":
        raise CommandError("the mouth is in live mode; switch it with /mouth/mode show first", 409)
    if isinstance(command, SetValue):
        board.set_value(command.motor, command.value)
    elif isinstance(command, StartPose):
        board.start_pose(command.motor, command.name, command.seconds)
    elif isinstance(command, RestCommand):
        if command.motor is None:
            board.rest_all()
        else:
            board.rest(command.motor)
    else:
        board.set_mouth_mode(command.mode)


def handle_osc(address: str, args: Sequence[object], sender: Destination, context: OscContext) -> None:
    """Apply or answer one OSC message; anything that can't be is logged (rate-limited) and dropped."""
    log = context.log
    try:
        command = osc_command(address, args, context.profiles)
        if isinstance(command, OscQuery):
            _answer(command, sender, context)
        elif command is not None:
            apply(command, context.board)
        # Any message Jack accepts shows the controller is in use, so it stays subscribed.
        context.subscribers.renew_from(sender[0])
    except CommandError as error:
        # Keyed by status so a flood of varied bad addresses stays one line per kind.
        log(f"ignored {error.status}", f"Ignored OSC {_clip(address)} {_clip(list(args))}: {_clip(error)}")
        return
    if isinstance(command, SetValue) and command.value != command.requested:
        log(f"clamped {command.motor}", f"Clamped OSC {_clip(address)} {command.requested} to {command.value}")


def _answer(query: OscQuery, sender: Destination, context: OscContext) -> None:
    reply_to = (sender[0], context.reply_port or sender[1])
    if isinstance(query, Ping | StatusRequest):
        messages = [("/jack/pong", [])] if isinstance(query, Ping) else state_messages(
            context.board.status(context.mumble_connected()))
        try:
            context.send(reply_to, messages)
        except OSError as error:
            context.log("reply failed", f"OSC reply to {reply_to} failed: {_clip(error)}")
    else:
        destination = (sender[0], query.port or reply_to[1])
        if isinstance(query, Unsubscribe):
            context.subscribers.unsubscribe(destination)
        elif not context.subscribers.subscribe(destination):
            context.log(
                "subscribers full",
                f"Refused OSC subscription from {destination}: already {MAX_SUBSCRIBERS} subscribers",
            )


def _clip(value: object) -> str:
    """Shorten text from the network so one packet can't fill the journal."""
    text = str(value)
    return text if len(text) <= MAX_LOGGED_CHARS else text[:MAX_LOGGED_CHARS] + "…"


def _is_button_value(value: object) -> bool:
    return isinstance(value, int | float)  # bool is an int


def _is_release(route: str, args: Sequence[object]) -> bool:
    """A subscribe or unsubscribe button's release: a single 0."""
    return route in ("/subscribe", "/unsubscribe") and len(args) == 1 and _is_button_value(args[0]) and args[0] == 0


def _check_finite(value: int | float) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise CommandError("button value must be a finite number")


def _is_button_route(route: str, parts: list[str]) -> bool:
    """Routes a TouchOSC button can drive: they take no arguments of their own."""
    if route in ("/rest", "/ping", "/status"):
        return True
    return bool(parts) and parts[0] in MOTOR_NAMES and (parts[1:] == ["rest"] or (len(parts) == 3 and parts[1] == "pose"))


def _pressed(args: Sequence[object]) -> bool:
    """A button sends nothing or one number: non-zero is a press, 0 a release. Anything else is a 400."""
    if len(args) > 1:
        raise CommandError("too many arguments for a button")
    if args and not _is_button_value(args[0]):
        raise CommandError(f"a button sends a number, got {_clip(args[0])}")
    if args:
        _check_finite(args[0])
    return not args or bool(args[0])


def _mouth_mode_toggle(route: str, args: Sequence[object]) -> SetMouthMode:
    if len(args) != 1 or not _is_button_value(args[0]):
        raise CommandError(f"{OSC_PREFIX}{route} takes one number")
    _check_finite(args[0])
    return SetMouthMode("show" if args[0] else "live")


def _pose_button(motor: str, name: str, profiles: Mapping[str, MotorProfile]) -> StartPose:
    _check_pose(motor, name, profiles)
    return StartPose(motor, name, None)


def _check_pose(motor: str, name: str, profiles: Mapping[str, MotorProfile]) -> None:
    if name not in profiles[motor].poses:
        raise CommandError(f"unknown pose {name!r} for {motor}; poses: {', '.join(sorted(profiles[motor].poses))}", 404)


def _query(route: str, args: Sequence[object]) -> OscQuery:
    if route in ("/ping", "/status"):
        if args:
            raise CommandError(f"too many arguments for {OSC_PREFIX}{route}")
        return Ping() if route == "/ping" else StatusRequest()
    if len(args) > 1:
        raise CommandError(f"too many arguments for {OSC_PREFIX}{route}")
    port = args[0] if args else None
    if port is not None:
        port = _port(port)
    return Subscribe(port) if route == "/subscribe" else Unsubscribe(port)


def _port(value: object) -> int:
    """A UDP port a controller can listen on; TouchOSC sends whole numbers as floats.

    Below 1024 is refused so a button left at its default press value of 1 can't subscribe port 1.
    """
    whole = isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if isinstance(value, bool) or not whole or not 1024 <= value <= 65535:
        raise CommandError(f"port must be a whole number 1024-65535, got {_clip(value)}")
    return int(value)


def _parts(route: str) -> list[str]:
    return [part for part in route.split("/") if part]


def _argument_names(parts: list[str]) -> list[str]:
    """OSC arguments are positional; these are their names, matching the HTTP JSON fields."""
    if parts == ["mouth", "mode"]:
        return ["mode"]
    if len(parts) == 2 and parts[1] == "pose":
        return ["name", "seconds"]
    if len(parts) == 1 and parts[0] in MOTOR_NAMES:
        return ["value"]
    return []


def _build(route: str, params: dict, profiles: Mapping[str, MotorProfile]) -> Command:
    parts = _parts(route)
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
        _check_pose(motor, name, profiles)
        seconds = None if params.get("seconds") is None else _number(params, "seconds")
        if seconds is not None and seconds <= 0:
            raise CommandError(f"seconds must be positive, got {seconds}")
        return StartPose(motor, name, seconds)
    if rest_of_route == ["rest"]:
        _only(params, set())
        return RestCommand(motor)
    raise CommandError(f"unknown route {route!r}", 404)


def _only(params: dict, allowed: set[str]) -> None:
    unexpected = set(params) - allowed
    if unexpected:
        raise CommandError(f"unexpected {', '.join(sorted(unexpected))}")


def _number(params: dict, key: str) -> float:
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
        raise CommandError(f"{key} must be a finite number, got {_clip(value)}")
    return number
