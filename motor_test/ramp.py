"""Triangle voltage ramps expressed as PCA9685 12-bit PWM duty counts."""

PWM_RESOLUTION = 4096
# A count of 4096 sets the PCA9685 "full off" bit, so 4095 is the highest usable duty.
PWM_MAX_COUNT = PWM_RESOLUTION - 1


def ramp_profile(peak_volts: float, supply_volts: float, steps: int) -> list[int]:
    """Return one cycle of a 0 -> peak -> 0 triangle ramp as duty counts.

    The cycle starts at 0 and peaks at index steps // 2. It omits the closing 0
    so cycles can be played back-to-back without repeating a sample.
    """
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if not 0 <= peak_volts <= supply_volts:
        raise ValueError(f"peak_volts must be between 0 and {supply_volts}, got {peak_volts}")
    if steps < 2 or steps % 2:
        raise ValueError(f"steps must be an even number >= 2, got {steps}")

    peak_count = min(round(peak_volts / supply_volts * PWM_RESOLUTION), PWM_MAX_COUNT)
    return [round(peak_count * (1 - abs(2 * i / steps - 1))) for i in range(steps)]
