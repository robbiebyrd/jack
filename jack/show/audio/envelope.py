"""How loud each frame is, smoothed like the Talking Skull board's peak detector but with chosen timings."""

import math
from array import array

FLOOR_DB = -90.0
FULL_SCALE = 32767


def rms_dbfs(frame: bytes) -> float:
    """RMS level of a 16-bit frame in dB relative to full scale, never below FLOOR_DB."""
    samples = array("h", frame)
    if not samples:
        return FLOOR_DB
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square == 0:
        return FLOOR_DB
    return max(FLOOR_DB, 10 * math.log10(mean_square / FULL_SCALE**2))


class EnvelopeFollower:
    """One-pole smoother in dB: rises with the attack time constant and falls with the release one."""

    def __init__(self, attack_s: float, release_s: float, tick_s: float):
        for name, value in (("attack_s", attack_s), ("release_s", release_s), ("tick_s", tick_s)):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        self._attack = math.exp(-tick_s / attack_s)
        self._release = math.exp(-tick_s / release_s)
        self.level_db = FLOOR_DB

    def update(self, db: float) -> float:
        """Move the smoothed level one tick toward `db` and return it."""
        coefficient = self._attack if db > self.level_db else self._release
        self.level_db = db + (self.level_db - db) * coefficient
        return self.level_db
