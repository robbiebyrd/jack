"""Interactive calibration: drive a motor at a typed voltage for a typed time, then brake."""

from collections.abc import Callable

from motor_test.mouth import CLOSE, RELAX, Segment, describe, hold, open_fully
from motor_test.ports import MotorOutput
from motor_test.ramp import segment_profile

# Longest single move, so a typo can't hold the motor stalled against an end stop for long.
MAX_MOVE_S = 3.0

USAGE = "enter '<volts> <seconds>' (e.g. '-4.5 0.8'), 'close', 'relax', 'open <seconds>', or 'q' to quit"

NAMED_POSES = {"close": CLOSE, "relax": RELAX}


def parse_command(line: str, supply_volts: float) -> tuple[Segment, ...] | None:
    """Parse a command into the segments it plays, or return None for 'q'/'quit'.

    Commands: '<volts> <seconds>', 'close', 'relax', or 'open <seconds>'.
    Raises ValueError, with a message fit to show the operator, for anything else.
    """
    words = line.split()
    if words in (["q"], ["quit"]):
        return None
    if len(words) == 1 and words[0] in NAMED_POSES:
        return NAMED_POSES[words[0]]
    if len(words) != 2:
        raise ValueError(USAGE)
    if words[0] == "open":
        return open_fully(_seconds(words[1]))
    try:
        volts = float(words[0])
    except ValueError:
        raise ValueError(USAGE) from None
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"volts must be between -{supply_volts} and {supply_volts}, got {words[0]}")
    return (hold(volts, _seconds(words[1])),)


def _seconds(word: str) -> float:
    try:
        seconds = float(word)
    except ValueError:
        raise ValueError(USAGE) from None
    if not 0 < seconds <= MAX_MOVE_S:
        raise ValueError(f"seconds must be more than 0 and at most {MAX_MOVE_S}, got {word}")
    return seconds



def run_calibration(
    motor: MotorOutput,
    supply_volts: float,
    step_s: float,
    read_line: Callable[[], str],
    write: Callable[[str], None],
    sleep: Callable[[float], None],
) -> None:
    """Perform typed moves until 'q' or end of input (EOFError from `read_line`).

    Each command plays step by step, one duty count per `step_s`, so ramps are
    smooth and durations must be whole steps.

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
                segments = parse_command(line, supply_volts)
                if segments is None:
                    return
                counts = segment_profile(segments, supply_volts, step_s)
            except ValueError as problem:
                write(str(problem))
                continue
            try:
                for count in counts:
                    motor.drive(count)
                    sleep(step_s)
            finally:
                motor.stop()
            write(f"moved B {describe(segments)}, braked")
    finally:
        motor.stop()
