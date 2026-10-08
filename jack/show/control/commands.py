"""The show commands OSC and HTTP both carry, and how the command board carries them out.

See "Show control" in SPEC.md. Routes become commands in routes.py; the OSC wire forms live in osc_protocol.py.
"""

from dataclasses import dataclass

from jack.show.control.control_board import ControlBoard


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


Command = SetValue | StartPose | RestCommand | SetMouthMode


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
