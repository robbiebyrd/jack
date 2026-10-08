"""What OSC messages mean to Jack: commands (through routes.py), queries, and TouchOSC's wire forms.

OSC alone answers /ping and /status and takes /subscribe [port] and /unsubscribe [port]; see "OSC replies and
feedback" in SPEC.md. TouchOSC forms (see "TouchOSC support"): ports may be whole-number floats; /rest,
/<motor>/rest, /ping, /status and /<motor>/pose/<name> are buttons (a press or no argument acts, a release of 0
is ignored, NaN or infinity is a 400); /subscribe and /unsubscribe ignore a release of 0 too, and their ports are
1024-65535; /mouth/mode and /mouth/mode/show take a number, non-zero for show and 0 for live.
"""

import logging
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from jack.show.control.commands import Command, CommandError, SetMouthMode, SetValue, StartPose, apply
from jack.show.control.control_board import ControlBoard
from jack.show.control.osc_feedback import MAX_SUBSCRIBERS, Destination, Message, Subscribers, state_messages
from jack.show.control.routes import argument_names, check_pose, command_for, route_parts
from jack.show.motion.motors import MOTOR_NAMES
from jack.show.motion.poses import MotorProfile
from jack.support.clip import clip
from jack.support.rate_limit import rate_limited

log = logging.getLogger(__name__)

OSC_PREFIX = "/jack"


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


_QUERY_ROUTES = ("/ping", "/status", "/subscribe", "/unsubscribe")


def osc_command(
    address: str, args: Sequence[object], profiles: Mapping[str, MotorProfile],
) -> Command | OscQuery | None:
    """Translate one OSC message; None means a button release, which is ignored."""
    if not address.startswith(OSC_PREFIX + "/"):
        raise CommandError(f"unknown address {address!r}", 404)
    route = address[len(OSC_PREFIX):]
    parts = route_parts(route)
    if route == "/mouth/mode/show" or (route == "/mouth/mode" and len(args) == 1 and _is_button_value(args[0])):
        return _mouth_mode_toggle(route, args)
    if _is_button_route(route, parts):
        if not _pressed(args):
            return None
        args = []
        if len(parts) == 3:
            check_pose(parts[0], parts[2], profiles)
            return StartPose(parts[0], parts[2], None)
    if route in _QUERY_ROUTES:
        return None if _is_release(route, args) else _query(route, args)
    names = argument_names(parts)
    # Build first, so an unknown address is a 404 even when it carries arguments.
    command = command_for(route, dict(zip(names, args, strict=False)), profiles)
    if len(args) > len(names):
        raise CommandError(f"too many arguments for {address}")
    return command


def handle_osc(address: str, args: Sequence[object], sender: Destination, context: OscContext) -> None:
    """Apply or answer one OSC message; anything that can't be is logged (rate-limited by kind) and dropped."""
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
        log.warning(
            "Ignored OSC %s %s: %s", clip(address), clip(list(args)), clip(error),
            **rate_limited(f"ignored {error.status}"),
        )
        return
    if isinstance(command, SetValue) and command.value != command.requested:
        log.warning(
            "Clamped OSC %s %s to %s", clip(address), command.requested, command.value,
            **rate_limited(f"clamped {command.motor}"),
        )


def _answer(query: OscQuery, sender: Destination, context: OscContext) -> None:
    reply_to = (sender[0], context.reply_port or sender[1])
    if isinstance(query, Ping | StatusRequest):
        messages = [("/jack/pong", [])] if isinstance(query, Ping) else state_messages(
            context.board.status(context.mumble_connected()))
        try:
            context.send(reply_to, messages)
        except OSError as error:
            log.warning("OSC reply to %s failed: %s", reply_to, clip(error), **rate_limited("reply failed"))
    else:
        destination = (sender[0], query.port or reply_to[1])
        if isinstance(query, Unsubscribe):
            context.subscribers.unsubscribe(destination)
        elif not context.subscribers.subscribe(destination):
            log.warning(
                "Refused OSC subscription from %s: already %d subscribers", destination, MAX_SUBSCRIBERS,
                **rate_limited("subscribers full"),
            )


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
    if not parts or parts[0] not in MOTOR_NAMES:
        return False
    return parts[1:] == ["rest"] or (len(parts) == 3 and parts[1] == "pose")


def _pressed(args: Sequence[object]) -> bool:
    """A button sends nothing or one number: non-zero is a press, 0 a release. Anything else is a 400."""
    if len(args) > 1:
        raise CommandError("too many arguments for a button")
    if args and not _is_button_value(args[0]):
        raise CommandError(f"a button sends a number, got {clip(args[0])}")
    if args:
        _check_finite(args[0])
    return not args or bool(args[0])


def _mouth_mode_toggle(route: str, args: Sequence[object]) -> SetMouthMode:
    if len(args) != 1 or not _is_button_value(args[0]):
        raise CommandError(f"{OSC_PREFIX}{route} takes one number")
    _check_finite(args[0])
    return SetMouthMode("show" if args[0] else "live")


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
        raise CommandError(f"port must be a whole number 1024-65535, got {clip(value)}")
    return int(value)
