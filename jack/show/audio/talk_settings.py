"""Every tunable setting of the talk loop in one place, checked when built.

Starting values are guesses to tune by eye with lipsync_wav.py, except the gates
(measured from Boss's voice) and the speeds (Boss's call). The mouth's voltages live in
poses.toml (its MotorProfile); these are lip-sync behaviour only.
See the "Mouth control" table in SPEC.md.
"""

from dataclasses import dataclass

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.pcm import TICK_S
from jack.show.motion.ramp import whole_steps


@dataclass(frozen=True)
class TalkSettings:
    open_curve: float = 2.0
    # Added to the voice's level before the gates: the mouth reacts to a quieter voice; the sound doesn't change.
    mouth_gain_db: float = 0.0
    attack_s: float = 0.01
    release_s: float = 0.04
    gate_open_db: float = -22.0
    gate_close_db: float = -27.0
    full_db: float = -14.0
    mouth_lead_ms: float = 0.0
    max_backlog_ms: float = 200.0

    def __post_init__(self) -> None:
        for name in ("attack_s", "release_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")
        if not self.gate_close_db < self.gate_open_db < self.full_db:
            raise ValueError(
                "gates must satisfy gate_close_db < gate_open_db < full_db, got "
                f"{self.gate_close_db}, {self.gate_open_db}, {self.full_db}"
            )
        if FLOOR_DB + self.mouth_gain_db >= self.gate_close_db:
            raise ValueError(
                f"mouth_gain_db {self.mouth_gain_db} would lift silence ({FLOOR_DB} dB) to the close gate "
                f"({self.gate_close_db} dB) and hold the mouth open"
            )
        if self.open_curve < 1.0:
            raise ValueError(f"open_curve must be at least 1.0, got {self.open_curve}")
        if self.mouth_lead_ms < 0:
            raise ValueError(f"mouth_lead_ms must not be negative, got {self.mouth_lead_ms}")
        # Each property below raises ValueError for a duration that isn't whole ticks.
        self.mouth_lead_ticks
        self.max_backlog_frames

    @property
    def mouth_lead_ticks(self) -> int:
        if self.mouth_lead_ms == 0:
            return 0
        return whole_steps(self.mouth_lead_ms / 1000, TICK_S)

    @property
    def max_backlog_frames(self) -> int:
        return whole_steps(self.max_backlog_ms / 1000, TICK_S)
