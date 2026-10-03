"""Turns one motor's commanded volts into its drive for each 20 ms tick, within its limits.

Driving harder is slew-limited; driving away from rest spends a stall budget (each tick
TICK_S / hold(volts)), and spending it forces a rest (stall protection) until commands stop; going to rest plays the motor's rest pulse, then brakes
or coasts. See "Command board" in SPEC.md.
"""

from dataclasses import dataclass

from motor_test.pcm import TICK_S
from motor_test.poses import MotorProfile

# Float sums of exact tick fractions can land a hair over 1; a whole budget is still within the hold.
BUDGET_TOLERANCE = 1e-9


@dataclass(frozen=True)
class Rest:
    """Not driven: "brake" shorts the leads, "coast" leaves them open."""

    mode: str


Drive = float | Rest


class MotorDriver:
    def __init__(self, profile: MotorProfile):
        self._profile = profile
        self._slew_per_tick = profile.slew_v_per_s * TICK_S
        self._pulse_ticks = profile.rest_pulse_ticks
        self._volts = 0.0
        self._budget_spent = 0.0
        self._pulse_left = 0
        self.max_hold_tripped = False

    def resume_from(self, volts: float) -> None:
        """Take over a motor something else left at `volts`, so the next update slews (or rests) from there."""
        self._volts = volts
        self._budget_spent = 0.0
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
        if self._volts != 0.0:
            self._budget_spent += TICK_S / self._profile.hold_s(self._volts)
        if self._budget_spent > 1.0 + BUDGET_TOLERANCE:
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
        # Refill even at exactly 0 V (a reversal can land there), so the next command gets its whole budget.
        self._budget_spent = 0.0
        if self._volts != 0.0:
            self._volts = 0.0
            self._pulse_left = self._pulse_ticks
        if self._pulse_left > 0:
            self._pulse_left -= 1
            return self._profile.rest_pulse_v
        return Rest(self._profile.rest)
