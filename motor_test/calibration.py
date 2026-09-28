"""Interactive calibration: drive a motor at a typed voltage for a typed time, then brake."""

from collections.abc import Callable
from dataclasses import dataclass

from motor_test.mouth import CLOSE, RELAX, Segment, open_fully
from motor_test.ports import MotorOutput
from motor_test.ramp import volts_to_count

# Longest single move, so a typo can't hold the motor stalled against an end stop for long.
MAX_MOVE_S = 3.0

USAGE = "enter '<volts> <seconds>' (e.g. '-4.5 0.8'), 'close', 'relax', 'open <seconds>', or 'q' to quit"

NAMED_POSES = {"close": CLOSE, "relax": RELAX}


@dataclass(frozen=True)
class Move:
    volts: float
    seconds: float


def parse_command(line: str, supply_volts: float) -> tuple[Move, ...] | None:
    """Parse a command into the moves it performs, or return None for 'q'/'quit'.

    Commands: '<volts> <seconds>', 'close', 'relax', or 'open <seconds>'.
    Raises ValueError, with a message fit to show the operator, for anything else.
    """
    words = line.split()
    if words in (["q"], ["quit"]):
        return None
    if len(words) == 1 and words[0] in NAMED_POSES:
        return _moves(NAMED_POSES[words[0]])
    if len(words) != 2:
        raise ValueError(USAGE)
    if words[0] == "open":
        return _moves(open_fully(_seconds(words[1])))
    try:
        volts = float(words[0])
    except ValueError:
        raise ValueError(USAGE) from None
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"volts must be between -{supply_volts} and {supply_volts}, got {words[0]}")
    return (Move(volts, _seconds(words[1])),)


def _seconds(word: str) -> float:
    try:
        seconds = float(word)
    except ValueError:
        raise ValueError(USAGE) from None
    if not 0 < seconds <= MAX_MOVE_S:
        raise ValueError(f"seconds must be more than 0 and at most {MAX_MOVE_S}, got {word}")
    return seconds


def _moves(segments: tuple[Segment, ...]) -> tuple[Move, ...]:
    return tuple(Move(volts, seconds) for volts, seconds in segments)


def run_calibration(
    motor: MotorOutput,
    supply_volts: float,
    read_line: Callable[[], str],
    write: Callable[[str], None],
    sleep: Callable[[float], None],
) -> None:
    """Perform typed moves until 'q' or end of input (EOFError from `read_line`).

    The motor is braked after every move and on every way out, including
    KeyboardInterrupt and I2C errors, which are re-raised after braking.
    """
    try:
        while True:
            try:
                line = read_line()
            except EOFError:
                return
            if not line.strip():
                continue
            try:
                moves = parse_command(line, supply_volts)
            except ValueError as problem:
                write(str(problem))
                continue
            if moves is None:
                return
            for move in moves:
                try:
                    motor.drive(volts_to_count(move.volts, supply_volts))
                    sleep(move.seconds)
                finally:
                    motor.stop()
                write(f"moved B {move.volts:+} V for {move.seconds} s, braked")
    finally:
        motor.stop()
