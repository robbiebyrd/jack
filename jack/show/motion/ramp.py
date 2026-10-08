"""Voltage waveforms expressed as signed PCA9685 12-bit PWM duty counts.

A negative count means the motor is driven backward at that duty.
"""

import math
from collections.abc import Sequence

PWM_RESOLUTION = 4096
# A count of 4096 sets the PCA9685 "full off" bit, so 4095 is the highest usable duty.
PWM_MAX_COUNT = PWM_RESOLUTION - 1


def segment_profile(segments: Sequence[tuple[float, float, float]], supply_volts: float, step_s: float) -> list[int]:
    """Return one cycle playing each `(start_volts, end_volts, seconds)` segment in order.

    A segment with equal start and end holds that level; otherwise it ramps
    linearly, reaching `end_volts` on its last step. There is one count per
    `step_s`, and every duration must be a whole number of steps so the cycle
    keeps its exact length.
    """
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if step_s <= 0:
        raise ValueError(f"step_s must be positive, got {step_s}")
    if not segments:
        raise ValueError("segments must contain at least one (start_volts, end_volts, seconds) segment")

    counts = []
    for start_volts, end_volts, seconds in segments:
        _check_signed_volts("start_volts", start_volts, supply_volts)
        _check_signed_volts("end_volts", end_volts, supply_volts)
        steps = whole_steps(seconds, step_s)
        counts += [
            volts_to_count(start_volts + (end_volts - start_volts) * step / steps, supply_volts)
            for step in range(1, steps + 1)
        ]
    return counts


def _check_signed_volts(name: str, volts: float, supply_volts: float) -> None:
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"{name} must be between -{supply_volts} and {supply_volts}, got {volts}")


def volts_to_count(volts: float, supply_volts: float) -> int:
    """Signed duty count for `volts`, capped so its magnitude never reaches the full-off bit."""
    count = min(round(abs(volts) / supply_volts * PWM_RESOLUTION), PWM_MAX_COUNT)
    return count if volts >= 0 else -count


def whole_steps(seconds: float, step_s: float) -> int:
    """How many `step_s` steps make `seconds`; it must be a whole, non-zero number."""
    steps = round(seconds / step_s)
    if steps < 1 or not math.isclose(steps * step_s, seconds):
        raise ValueError(f"{seconds} s is not a whole, non-zero number of {step_s} s steps")
    return steps
