"""Turns the audio's smoothed level into mouth voltage, one 20 ms tick at a time.

The voltage follows the audio's amplitude, as on the Talking Skull board: max_v x amplitude x gain, capped at
max_v. Below min_v the mouth gets a short closing pull (its rest pulse), then brakes; it holds closed unpowered.
Opening wider is slew-limited, and an opening that spends the mouth's stall budget closes until a quiet moment.
See "Mouth control" in SPEC.md.
"""

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.pcm import TICK_S
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.poses import MotorProfile
from jack.show.motion.stall_budget import StallBudget


def check_mouth_gain(settings: TalkSettings, mouth: MotorProfile) -> None:
    """Refuse a gain at which silence would open the mouth: it would then never close."""
    silence_v = mouth.max_v * 10 ** ((FLOOR_DB + settings.mouth_gain_db) / 20)
    if silence_v >= mouth.min_v:
        raise ValueError(
            f"mouth_gain_db {settings.mouth_gain_db} would open the mouth on silence ({FLOOR_DB} dBFS maps to "
            f"{silence_v:.2f} V, at or above the mouth's min_v {mouth.min_v} V)"
        )


class MouthController:
    """Signed volts for the mouth from the smoothed level (dBFS), opening in the direction of its `sign`."""

    def __init__(self, settings: TalkSettings, mouth: MotorProfile):
        check_mouth_gain(settings, mouth)
        self._mouth = mouth
        self._gain = 10 ** (settings.mouth_gain_db / 20)
        self._slew_per_tick = mouth.slew_v_per_s * TICK_S
        self._pull_ticks = mouth.rest_pulse_ticks
        self._magnitude = 0.0
        self._pull_left = 0
        self._budget = StallBudget(mouth)
        # Set when an opening spent the budget; the mouth stays closed until the sound drops below min_v.
        self._waiting_for_quiet = False

    def update(self, level_db: float) -> float:
        """Advance one tick with the current smoothed level and return the mouth voltage."""
        target = min(self._mouth.max_v, self._mouth.max_v * 10 ** (level_db / 20) * self._gain)
        if target < self._mouth.min_v:
            self._waiting_for_quiet = False
            return self._close()
        if self._waiting_for_quiet:
            return self._close()
        self._pull_left = 0
        # Opening wider is slew-limited because stepping straight to fully open strains the motor.
        self._magnitude = min(target, self._magnitude + self._slew_per_tick)
        volts = self._mouth.sign * self._magnitude
        self._budget.spend(volts)
        if not self._budget.exhausted:
            return volts
        self._waiting_for_quiet = True
        return self._close()

    def _close(self) -> float:
        """The closing pull after an opening, then 0 V (short brake)."""
        if self._magnitude > 0:
            self._magnitude = 0.0
            self._pull_left = self._pull_ticks
            self._budget.refill()
        if self._pull_left > 0:
            self._pull_left -= 1
            return self._mouth.rest_pulse_v
        return 0.0
