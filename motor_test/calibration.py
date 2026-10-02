"""Interactive calibration: drive one motor at a typed voltage, or one of its poses, then brake."""

import math
from collections.abc import Callable

from motor_test.mouth import Segment, describe, hold, ramp
from motor_test.ports import MotorOutput
from motor_test.poses import MotorProfile, Pose
from motor_test.ramp import segment_profile

# Longest single move, so a typo can't hold the motor stalled against an end stop for long.
MAX_MOVE_S = 3.0

USAGE = "enter '<volts> <seconds>' (e.g. '-4.5 0.8'), '<pose>' or '<pose> <seconds>', or 'q' to quit"


def parse_command(line: str, supply_volts: float, profile: MotorProfile, step_s: float) -> tuple[Segment, ...] | None:
    """Parse a command into the segments it plays, or return None for 'q'/'quit'.

    Raises ValueError, with a message fit to show the operator, for anything else.
    """
    words = line.split()
    if words in (["q"], ["quit"]):
        return None
    if words and words[0] in profile.poses:
        if len(words) > 2:
            raise ValueError(_usage(profile))
        pose = profile.poses[words[0]]
        seconds = _seconds(words[1]) if len(words) == 2 else _seconds(str(pose.seconds))
        return pose_segments(pose, seconds, profile.slew_v_per_s, step_s)
    if len(words) != 2:
        raise ValueError(_usage(profile))
    try:
        volts = float(words[0])
    except ValueError:
        raise ValueError(_usage(profile)) from None
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"volts must be between -{supply_volts} and {supply_volts}, got {words[0]}")
    return (hold(volts, _seconds(words[1])),)


def pose_segments(pose: Pose, seconds: float, slew_v_per_s: float, step_s: float) -> tuple[Segment, ...]:
    """Ramp from 0 V to the pose's volts, rounded up to whole steps (at least one) so it is never faster than the slew limit, then hold."""
    ramp_steps = max(1, math.ceil(abs(pose.volts) / slew_v_per_s / step_s - 1e-9))
    return (ramp(0.0, pose.volts, ramp_steps * step_s), hold(pose.volts, seconds))


def _usage(profile: MotorProfile) -> str:
    return f"{USAGE}; poses: {', '.join(sorted(profile.poses))}"


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
    motor_name: str,
    profile: MotorProfile,
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
                segments = parse_command(line, supply_volts, profile, step_s)
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
            write(f"moved {motor_name} {describe(segments)}, braked")
    finally:
        motor.stop()
