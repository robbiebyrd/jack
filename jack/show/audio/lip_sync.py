"""Turns the audio's smoothed level into the volts the voice asks of the mouth, one 20 ms tick at a time.

The target follows the audio's amplitude, as on the Talking Skull board: max_v x amplitude x gain, capped at
max_v, in the direction of the mouth's `sign`. Below min_v the voice asks for rest (None). The mouth's
MotorDriver then applies the slew limit, the stall budget and the closing pull (its rest pulse), exactly as
it does for show commands, so lip sync and show control share one driver. See "Mouth control" in SPEC.md.
"""

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.poses import MotorProfile


def check_mouth_gain(settings: TalkSettings, mouth: MotorProfile) -> None:
    """Refuse a gain at which silence would open the mouth: it would then never close."""
    silence_v = mouth.max_v * 10 ** ((FLOOR_DB + settings.mouth_gain_db) / 20)
    if silence_v >= mouth.min_v:
        raise ValueError(
            f"mouth_gain_db {settings.mouth_gain_db} would open the mouth on silence ({FLOOR_DB} dBFS maps to "
            f"{silence_v:.2f} V, at or above the mouth's min_v {mouth.min_v} V)"
        )


class LipSync:
    """The mouth's target volts for a smoothed level (dBFS): signed in the mouth's opening direction, None for rest."""

    def __init__(self, settings: TalkSettings, mouth: MotorProfile):
        check_mouth_gain(settings, mouth)
        self._mouth = mouth
        self._gain = 10 ** (settings.mouth_gain_db / 20)

    def target(self, level_db: float) -> float | None:
        """Volts in proportion to the amplitude at `level_db`, capped at max_v; None (rest) below min_v."""
        volts = min(self._mouth.max_v, self._mouth.max_v * 10 ** (level_db / 20) * self._gain)
        if volts < self._mouth.min_v:
            return None
        return self._mouth.sign * volts
