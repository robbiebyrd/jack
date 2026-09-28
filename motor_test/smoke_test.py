"""Plays a signed duty-count profile on each motor output until the process is stopped."""

from collections.abc import Callable, Sequence

from motor_test.attempt_all import attempt_all
from motor_test.ports import MotorOutput


def run_profiles_loop(
    profiles: Sequence[tuple[MotorOutput, Sequence[int]]],
    step_s: float,
    sleep: Callable[[float], None],
    on_cycle: Callable[[], None],
) -> None:
    """Drive every motor through its own profile in lockstep, cycle after cycle, forever.

    Step i drives each motor with its profile's i-th count, then waits `step_s`.
    `on_cycle` runs after every completed cycle, which proves the loop is alive.

    Every motor is always stopped on the way out, whether the loop ends through an
    error, KeyboardInterrupt, or SystemExit raised by a SIGTERM handler, and a
    failure stopping one motor does not skip the others.
    """
    if not profiles:
        raise ValueError("profiles must contain at least one motor")
    steps = len(profiles[0][1])
    if steps == 0 or any(len(counts) != steps for _, counts in profiles):
        raise ValueError("every profile must have the same, non-zero number of steps")

    try:
        while True:
            for step in range(steps):
                for motor, counts in profiles:
                    motor.drive(counts[step])
                sleep(step_s)
            on_cycle()
    finally:
        attempt_all(motor.stop for motor, _ in profiles)
