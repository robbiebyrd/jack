"""Mouth poses for the animatronic head (motor B), as (volts, seconds) segments.

Values come from Boss's calibration (SPEC.md): closed and relaxed open hold
without power once reached; fully open holds only while -6 V is applied, which
stalls the motor, so keep it short.
"""

Segment = tuple[float, float]

# Closes the mouth from any pose.
CLOSE: tuple[Segment, ...] = ((1.0, 0.25),)
# Drives the mouth past relaxed open; on release it settles there.
RELAX: tuple[Segment, ...] = ((-2.0, 0.5),)


def open_fully(seconds: float) -> tuple[Segment, ...]:
    """Hold the mouth fully open for `seconds`."""
    return ((-6.0, _positive(seconds)),)


def rest(seconds: float) -> tuple[Segment, ...]:
    """Apply no drive for `seconds`, leaving the mouth in the pose it last reached."""
    return ((0.0, _positive(seconds)),)


def _positive(seconds: float) -> float:
    if seconds <= 0:
        raise ValueError(f"seconds must be positive, got {seconds}")
    return seconds
