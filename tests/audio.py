"""Builders for PCM test frames in the talk loop's format."""

import math
from array import array

from motor_test.pcm import FRAME_SAMPLES, SAMPLE_RATE_HZ


def constant_frame(value: int) -> bytes:
    """One frame with every sample equal to `value`."""
    return array("h", [value] * FRAME_SAMPLES).tobytes()


def sine_frame(amplitude: int, frequency_hz: float = 1000.0) -> bytes:
    """One frame of a sine wave; 1 kHz fits exactly 20 whole cycles in a 20 ms frame."""
    return array(
        "h",
        (round(amplitude * math.sin(2 * math.pi * frequency_hz * i / SAMPLE_RATE_HZ)) for i in range(FRAME_SAMPLES)),
    ).tobytes()
