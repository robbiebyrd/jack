"""Turns smoothed loudness into mouth voltage, one 20 ms tick at a time.

Closed until the level reaches the open gate; open in proportion to loudness while it
stays above the (lower) close gate; then a short closing pulse. An opening that spends the mouth's
stall budget (its measured holds) closes and stays closed until a pause. See "Mouth control" in SPEC.md.
"""

from enum import Enum

from jack.show.audio.pcm import TICK_S
from jack.show.motion.poses import MotorProfile
from jack.show.motion.stall_budget import StallBudget
from jack.show.audio.talk_settings import TalkSettings


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    CLOSING = "closing"


class MouthController:
    """State machine from level (dBFS) to signed volts for the mouth motor, opening in the direction of its `sign`."""

    def __init__(self, settings: TalkSettings, mouth: MotorProfile):
        self._settings = settings
        self._mouth = mouth
        self._slew_per_tick = mouth.slew_v_per_s * TICK_S
        self._close_ticks = mouth.rest_pulse_ticks
        self._state = _State.CLOSED
        self._magnitude = 0.0
        self._close_ticks_left = 0
        self._budget = StallBudget(mouth)
        # Set when an opening spent the budget; the mouth stays closed until the level drops below the close gate.
        self._waiting_for_pause = False

    def update(self, level_db: float) -> float:
        """Advance one tick with the current smoothed level and return the mouth voltage."""
        if level_db < self._settings.gate_close_db:
            self._waiting_for_pause = False
        if level_db >= self._settings.gate_open_db and not self._waiting_for_pause:
            self._state = _State.OPEN
        elif self._state is _State.OPEN and level_db < self._settings.gate_close_db:
            self._start_closing()

        if self._state is _State.OPEN:
            volts = self._mouth.sign * self._open_magnitude(level_db)
            self._budget.spend(volts)
            if not self._budget.exhausted:
                return volts
            self._waiting_for_pause = True
            self._start_closing()
        if self._state is _State.CLOSING:
            return self._closing_volts()
        return 0.0

    def _open_magnitude(self, level_db: float) -> float:
        s = self._settings
        loudness = min(1.0, max(0.0, (level_db - s.gate_open_db) / (s.full_db - s.gate_open_db)))
        # The curve keeps medium syllables near relaxed open so only loud peaks open wide.
        loudness = loudness**s.open_curve
        target = self._mouth.min_v + (self._mouth.max_v - self._mouth.min_v) * loudness
        # Opening wider is slew-limited because stepping straight to fully open strains the motor.
        self._magnitude = min(target, self._magnitude + self._slew_per_tick)
        return self._magnitude

    def _start_closing(self) -> None:
        self._state = _State.CLOSING if self._close_ticks else _State.CLOSED
        self._close_ticks_left = self._close_ticks
        self._magnitude = 0.0
        self._budget.refill()

    def _closing_volts(self) -> float:
        self._close_ticks_left -= 1
        if self._close_ticks_left == 0:
            self._state = _State.CLOSED
        return self._mouth.rest_pulse_v
