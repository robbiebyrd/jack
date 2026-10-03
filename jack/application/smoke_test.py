"""Plays signed duty-count profiles on motor outputs until the process is stopped."""

from collections.abc import Callable, Sequence

from jack.support.attempt_all import attempt_all
from jack.application.ports import MotorOutput


def run_profiles_loop(
    profiles: Sequence[tuple[MotorOutput, Sequence[int]]],
    step_s: float,
    sleep: Callable[[float], None],
    on_cycle: Callable[[], None],
) -> None:
    """Drive every motor through its own fixed profile in lockstep, cycle after cycle, forever.

    Profiles are checked before any motor is touched.
    """
    if not profiles:
        raise ValueError("profiles must contain at least one motor")
    counts = [list(motor_counts) for _, motor_counts in profiles]
    _check_cycle(counts, len(profiles))
    run_generated_loop([motor for motor, _ in profiles], lambda: counts, step_s, sleep, on_cycle)


def run_generated_loop(
    motors: Sequence[MotorOutput],
    next_cycle: Callable[[], Sequence[Sequence[int]]],
    step_s: float,
    sleep: Callable[[float], None],
    on_cycle: Callable[[], None],
) -> None:
    """Each cycle, get one count list per motor from `next_cycle` and play them in lockstep, forever.

    Step i drives each motor with its list's i-th count, then waits `step_s`.
    `on_cycle` runs after every completed cycle, which proves the loop is alive.

    Every motor is always stopped on the way out, whether the loop ends through an
    error (including an unplayable cycle), KeyboardInterrupt, or SystemExit raised
    by a SIGTERM handler, and a failure stopping one motor does not skip the others.
    """
    if not motors:
        raise ValueError("motors must contain at least one motor")

    try:
        while True:
            cycle = next_cycle()
            _check_cycle(cycle, len(motors))
            for step in range(len(cycle[0])):
                for motor, counts in zip(motors, cycle):
                    motor.drive(counts[step])
                sleep(step_s)
            on_cycle()
    finally:
        attempt_all(motor.stop for motor in motors)


def _check_cycle(cycle: Sequence[Sequence[int]], motor_count: int) -> None:
    if len(cycle) != motor_count:
        raise ValueError(f"expected one count list per motor ({motor_count}), got {len(cycle)}")
    steps = len(cycle[0]) if cycle else 0
    if steps == 0 or any(len(counts) != steps for counts in cycle):
        raise ValueError("every profile must have the same, non-zero number of steps")
