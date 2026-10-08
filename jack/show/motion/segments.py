"""Voltage segments: what the calibration tool plays, as (start_volts, end_volts, seconds) triples."""

Segment = tuple[float, float, float]

# Playback step: the player sends one duty count per step, so every segment
# duration must be a whole number of these.
STEP_S = 0.05


def hold(volts: float, seconds: float) -> Segment:
    """Hold `volts` for `seconds`."""
    return (volts, volts, seconds)


def ramp(start_volts: float, end_volts: float, seconds: float) -> Segment:
    """Move linearly from `start_volts` to `end_volts` over `seconds`."""
    return (start_volts, end_volts, seconds)


def describe(segments: tuple[Segment, ...]) -> str:
    """Human-readable summary, e.g. '+1.0 V for 0.25 s, 0.0 -> -6.0 V over 0.25 s'."""
    return ", ".join(
        f"{start:+} V for {seconds} s" if start == end else f"{start:+} -> {end:+} V over {seconds} s"
        for start, end, seconds in segments
    )
