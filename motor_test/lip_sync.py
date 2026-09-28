"""Turns smoothed loudness into mouth voltage, one 20 ms tick at a time.

Closed until the level reaches the open gate; open in proportion to loudness while it
stays above the (lower) close gate; then a short closing pulse. See "Mouth control" in SPEC.md.
"""

from enum import Enum

from motor_test.pcm import TICK_S
from motor_test.talk_settings import TalkSettings


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    CLOSING = "closing"


class MouthController:
    """State machine from level (dBFS) to signed volts for the mouth motor; negative opens."""

    def __init__(self, settings: TalkSettings):
        self._settings = settings
        self._slew_per_tick = settings.open_slew_v_per_s * TICK_S
        self._state = _State.CLOSED
        self._magnitude = 0.0
        self._close_ticks_left = 0
        self._stall_ticks = 0
        self._stall_capped = False

    def update(self, level_db: float) -> float:
        """Advance one tick with the current smoothed level and return the mouth voltage."""
        if level_db >= self._settings.gate_open_db:
            self._state = _State.OPEN
        elif self._state is _State.OPEN and level_db < self._settings.gate_close_db:
            self._start_closing()

        if self._state is _State.OPEN:
            return -self._open_magnitude(level_db)
        if self._state is _State.CLOSING:
            return self._closing_volts()
        return 0.0

    def _open_magnitude(self, level_db: float) -> float:
        s = self._settings
        loudness = min(1.0, max(0.0, (level_db - s.gate_open_db) / (s.full_db - s.gate_open_db)))
        target = s.open_min_v + (s.open_max_v - s.open_min_v) * loudness
        if self._stall_capped:
            target = min(target, s.open_min_v)
        # Opening wider is slew-limited because stepping straight to fully open strains the motor.
        self._magnitude = min(target, self._magnitude + self._slew_per_tick)
        self._guard_stall()
        return self._magnitude

    def _guard_stall(self) -> None:
        """Cap the opening at relaxed open once it has pushed past stall_v for too long without a break."""
        s = self._settings
        if self._magnitude <= s.stall_v:
            self._stall_ticks = 0
            return
        self._stall_ticks += 1
        if self._stall_ticks > s.max_stall_ticks:
            self._stall_capped = True
            self._magnitude = min(self._magnitude, s.open_min_v)

    def _start_closing(self) -> None:
        self._state = _State.CLOSING
        self._close_ticks_left = self._settings.close_ticks
        self._magnitude = 0.0
        self._stall_ticks = 0
        self._stall_capped = False

    def _closing_volts(self) -> float:
        self._close_ticks_left -= 1
        if self._close_ticks_left == 0:
            self._state = _State.CLOSED
        return self._settings.close_v
