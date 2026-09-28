"""Voltage waveforms expressed as signed PCA9685 12-bit PWM duty counts.

A negative count means the motor is driven backward at that duty.
"""

import math
from collections.abc import Sequence

PWM_RESOLUTION = 4096
# A count of 4096 sets the PCA9685 "full off" bit, so 4095 is the highest usable duty.
PWM_MAX_COUNT = PWM_RESOLUTION - 1


def ramp_profile(peak_volts: float, supply_volts: float, steps: int) -> list[int]:
    """Return one cycle of a 0 -> peak -> 0 triangle ramp as duty counts.

    The cycle starts at 0 and peaks at index steps // 2. It omits the closing 0
    so cycles can be played back-to-back without repeating a sample.
    """
    _check_cycle(supply_volts, steps)
    if not 0 <= peak_volts <= supply_volts:
        raise ValueError(f"peak_volts must be between 0 and {supply_volts}, got {peak_volts}")

    peak_count = _volts_to_count(peak_volts, supply_volts)
    return [round(peak_count * (1 - abs(2 * i / steps - 1))) for i in range(steps)]


def square_profile(high_volts: float, low_volts: float, supply_volts: float, steps: int) -> list[int]:
    """Return one cycle that holds `high_volts` for the first half, then `low_volts` for the second."""
    _check_cycle(supply_volts, steps)
    _check_signed_volts("high_volts", high_volts, supply_volts)
    _check_signed_volts("low_volts", low_volts, supply_volts)

    half = steps // 2
    return [_volts_to_count(high_volts, supply_volts)] * half + [_volts_to_count(low_volts, supply_volts)] * half


def constant_profile(volts: float, supply_volts: float, steps: int) -> list[int]:
    """Return one cycle that holds `volts` at every step."""
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if steps < 1:
        raise ValueError(f"steps must be at least 1, got {steps}")
    _check_signed_volts("volts", volts, supply_volts)
    return [_volts_to_count(volts, supply_volts)] * steps


def hold_sequence_profile(
    segments: Sequence[tuple[float, float]], supply_volts: float, step_s: float
) -> list[int]:
    """Return one cycle that holds each `(volts, seconds)` segment in order, one count per `step_s`.

    Every duration must be a whole number of steps so the cycle keeps its exact length.
    """
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if step_s <= 0:
        raise ValueError(f"step_s must be positive, got {step_s}")
    if not segments:
        raise ValueError("segments must contain at least one (volts, seconds) pair")

    counts = []
    for volts, seconds in segments:
        _check_signed_volts("volts", volts, supply_volts)
        steps = round(seconds / step_s)
        if steps < 1 or not math.isclose(steps * step_s, seconds):
            raise ValueError(f"{seconds} s is not a whole, non-zero number of {step_s} s steps")
        counts += [_volts_to_count(volts, supply_volts)] * steps
    return counts


def _check_cycle(supply_volts: float, steps: int) -> None:
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if steps < 2 or steps % 2:
        raise ValueError(f"steps must be an even number >= 2, got {steps}")


def _check_signed_volts(name: str, volts: float, supply_volts: float) -> None:
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"{name} must be between -{supply_volts} and {supply_volts}, got {volts}")


def _volts_to_count(volts: float, supply_volts: float) -> int:
    """Signed duty count for `volts`, capped so its magnitude never reaches the full-off bit."""
    count = min(round(abs(volts) / supply_volts * PWM_RESOLUTION), PWM_MAX_COUNT)
    return count if volts >= 0 else -count
