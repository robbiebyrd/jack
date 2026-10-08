"""Fixed-size PCM audio frames: what the talk loop plays, measures and mixes every tick.

Audio is mono, signed 16-bit, native byte order (little-endian on the Pi and the Mac),
48 kHz: the format Mumble decodes to.
"""

from array import array
from collections.abc import Sequence

SAMPLE_RATE_HZ = 48000
TICK_S = 0.02
FRAME_SAMPLES = round(SAMPLE_RATE_HZ * TICK_S)
SAMPLE_BYTES = 2
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES
TICKS_PER_SECOND = round(1 / TICK_S)
SAMPLE_MIN = -32768
SAMPLE_MAX = 32767


def silence() -> bytes:
    """One frame of digital silence."""
    return bytes(FRAME_BYTES)


def mix(frames: Sequence[bytes]) -> bytes:
    """Sum frames sample by sample, clipping to the 16-bit range."""
    if not frames:
        raise ValueError("mix needs at least one frame")
    for frame in frames:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frames must be {FRAME_BYTES} bytes, got {len(frame)}")
    if len(frames) == 1:
        return frames[0]
    decoded = [array("h", frame) for frame in frames]
    mixed = array("h", (max(SAMPLE_MIN, min(SAMPLE_MAX, sum(samples))) for samples in zip(*decoded, strict=True)))
    return mixed.tobytes()
