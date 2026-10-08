"""Every tunable setting of the talk loop in one place, checked when built.

Starting values are guesses to tune by eye with lipsync_wav.py, except the mouth gain
(set from Boss's measured voice) and the speeds (Boss's call). The mouth's voltages live in
poses.toml (its MotorProfile); these are lip-sync behaviour only.
See the "Mouth control" table in SPEC.md.
"""

from dataclasses import dataclass

from jack.show.audio.pcm import TICK_S
from jack.show.motion.ramp import whole_steps


@dataclass(frozen=True)
class TalkSettings:
    # The mouth's volts are max_v x amplitude x this gain (as a multiplier), capped at max_v: +14 dB opens the
    # mouth fully at -14 dBFS, the full-open point measured from Boss's voice. Changes the mouth, not the sound.
    mouth_gain_db: float = 14.0
    attack_s: float = 0.01
    release_s: float = 0.04
    mouth_lead_ms: float = 0.0
    max_backlog_ms: float = 200.0

    def __post_init__(self) -> None:
        for name in ("attack_s", "release_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")
        if self.mouth_lead_ms < 0:
            raise ValueError(f"mouth_lead_ms must not be negative, got {self.mouth_lead_ms}")
        # Both properties raise ValueError for a duration that isn't whole ticks.
        _ = (self.mouth_lead_ticks, self.max_backlog_frames)

    @property
    def mouth_lead_ticks(self) -> int:
        if self.mouth_lead_ms == 0:
            return 0
        return whole_steps(self.mouth_lead_ms / 1000, TICK_S)

    @property
    def max_backlog_frames(self) -> int:
        return whole_steps(self.max_backlog_ms / 1000, TICK_S)
