"""Turns one motor's commanded volts into its drive for each 20 ms tick, within its limits.

Driving harder is slew-limited; driving away from rest longer than max_hold_s forces a rest
(stall protection) until commands stop; going to rest plays the motor's rest pulse, then brakes
or coasts. See "Command board" in SPEC.md.
"""

from dataclasses import dataclass

from motor_test.pcm import TICK_S
from motor_test.poses import MotorProfile
from motor_test.ramp import whole_steps


@dataclass(frozen=True)
class Rest:
    """Not driven: "brake" shorts the leads, "coast" leaves them open."""

    mode: str


Drive = float | Rest


class MotorDriver:
    def __init__(self, profile: MotorProfile):
        self._profile = profile
        self._slew_per_tick = profile.slew_v_per_s * TICK_S
        self._max_hold_ticks = whole_steps(profile.max_hold_s, TICK_S)
        self._pulse_ticks = whole_steps(profile.rest_pulse_s, TICK_S) if profile.rest_pulse_s > 0 else 0
        self._volts = 0.0
        self._held_ticks = 0
        self._pulse_left = 0
        self.max_hold_tripped = False

    def update(self, target: float | None) -> Drive:
        """Advance one tick toward `target` volts (None or 0 = rest) and return the drive."""
        if target is None or target == 0.0:
            self.max_hold_tripped = False
            return self._to_rest()
        if self.max_hold_tripped:
            return self._to_rest()
        self._pulse_left = 0
        self._volts = self._slewed(target)
        self._held_ticks += 1
        if self._held_ticks > self._max_hold_ticks:
            self.max_hold_tripped = True
            return self._to_rest()
        return self._volts

    def _slewed(self, target: float) -> float:
        current = self._volts
        easing_off = abs(target) <= abs(current) and (target >= 0) == (current >= 0)
        if easing_off:
            return target
        step = max(-self._slew_per_tick, min(self._slew_per_tick, target - current))
        return current + step

    def _to_rest(self) -> Drive:
        if self._volts != 0.0:
            self._volts = 0.0
            self._held_ticks = 0
            self._pulse_left = self._pulse_ticks
        if self._pulse_left > 0:
            self._pulse_left -= 1
            return self._profile.rest_pulse_v
        return Rest(self._profile.rest)
