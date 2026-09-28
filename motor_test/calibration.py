"""Interactive calibration: drive a motor at a typed voltage for a typed time, then brake."""

from collections.abc import Callable
from dataclasses import dataclass

from motor_test.ports import MotorOutput
from motor_test.ramp import volts_to_count

# Longest single move, so a typo can't hold the motor stalled against an end stop for long.
MAX_MOVE_S = 3.0

USAGE = "enter '<volts> <seconds>' (e.g. '-4.5 0.8'), or 'q' to quit"


@dataclass(frozen=True)
class Move:
    volts: float
    seconds: float


def parse_command(line: str, supply_volts: float) -> Move | None:
    """Parse '<volts> <seconds>' into a Move, or return None for 'q'/'quit'.

    Raises ValueError, with a message fit to show the operator, for anything else.
    """
    words = line.split()
    if words in (["q"], ["quit"]):
        return None
    if len(words) != 2:
        raise ValueError(USAGE)
    try:
        volts, seconds = float(words[0]), float(words[1])
    except ValueError:
        raise ValueError(USAGE) from None
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"volts must be between -{supply_volts} and {supply_volts}, got {words[0]}")
    if not 0 < seconds <= MAX_MOVE_S:
        raise ValueError(f"seconds must be more than 0 and at most {MAX_MOVE_S}, got {words[1]}")
    return Move(volts, seconds)


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
                move = parse_command(line, supply_volts)
            except ValueError as problem:
                write(str(problem))
                continue
            if move is None:
                return
            try:
                motor.drive(volts_to_count(move.volts, supply_volts))
                sleep(move.seconds)
            finally:
                motor.stop()
            write(f"moved B {move.volts:+} V for {move.seconds} s, braked")
    finally:
        motor.stop()
