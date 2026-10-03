"""Mouth poses for the animatronic head (motor A), as (start_volts, end_volts, seconds) segments.

Values come from Boss's calibration (SPEC.md): closed and relaxed open hold
without power once reached; fully open holds only while -6 V is applied, which
stalls the motor, so keep it short. Stepping straight to -6 V strains the
motor, so fully open ramps up to it.
"""

Segment = tuple[float, float, float]

# Playback step: the player sends one duty count per step, so every pose
# duration must be a whole number of these.
STEP_S = 0.05

# How long fully open takes to ramp from 0 V to -6 V.
OPEN_RAMP_S = 0.25


def hold(volts: float, seconds: float) -> Segment:
    """Hold `volts` for `seconds`."""
    return (volts, volts, seconds)


def ramp(start_volts: float, end_volts: float, seconds: float) -> Segment:
    """Move linearly from `start_volts` to `end_volts` over `seconds`."""
    return (start_volts, end_volts, seconds)


# Closes the mouth from any pose.
CLOSE: tuple[Segment, ...] = (hold(1.0, 0.25),)
# Drives the mouth past relaxed open; on release it settles there.
RELAX: tuple[Segment, ...] = (hold(-2.0, 0.5),)


def open_fully(seconds: float) -> tuple[Segment, ...]:
    """Ramp up to fully open, then hold it for `seconds`."""
    return (ramp(0.0, -6.0, OPEN_RAMP_S), hold(-6.0, _positive(seconds)))


def rest(seconds: float) -> tuple[Segment, ...]:
    """Apply no drive for `seconds`, leaving the mouth in the pose it last reached."""
    return (hold(0.0, _positive(seconds)),)


def describe(segments: tuple[Segment, ...]) -> str:
    """Human-readable summary, e.g. '+1.0 V for 0.25 s, 0.0 -> -6.0 V over 0.25 s'."""
    return ", ".join(
        f"{start:+} V for {seconds} s" if start == end else f"{start:+} -> {end:+} V over {seconds} s"
        for start, end, seconds in segments
    )


def _positive(seconds: float) -> float:
    if seconds <= 0:
        raise ValueError(f"seconds must be positive, got {seconds}")
    return seconds
