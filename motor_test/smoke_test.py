"""Plays a duty-count ramp on a motor output until the process is stopped."""

from collections.abc import Callable, Sequence

from motor_test.ports import MotorOutput


def run_ramp_loop(
    motor: MotorOutput,
    counts: Sequence[int],
    step_s: float,
    sleep: Callable[[float], None],
    on_cycle: Callable[[], None],
) -> None:
    """Drive `motor` forward through `counts` cycle after cycle, forever.

    `on_cycle` runs after every completed cycle, which proves the loop is alive.

    The motor is always stopped on the way out, whether the loop ends through
    an error, KeyboardInterrupt, or SystemExit raised by a SIGTERM handler.
    """
    if not counts:
        raise ValueError("counts must contain at least one duty count")

    try:
        motor.set_forward()
        while True:
            for count in counts:
                motor.set_duty(count)
                sleep(step_s)
            on_cycle()
    finally:
        motor.stop()
