"""How much of a motor's measured holds its current drive has used (stall protection).

Each 20 ms tick at `volts` spends TICK_S / hold(volts) of a whole budget of 1; ticks at 0 V spend
nothing. Any rest refills it. Shared by show control (motor_driver.py) and lip sync (lip_sync.py).
See "Command board" in SPEC.md.
"""

from motor_test.pcm import TICK_S
from motor_test.poses import MotorProfile

# Float sums of exact tick fractions can land a hair over 1; a whole budget is still within the hold.
BUDGET_TOLERANCE = 1e-9


class StallBudget:
    def __init__(self, profile: MotorProfile):
        self._profile = profile
        self._spent = 0.0

    def spend(self, volts: float) -> None:
        """Spend one tick driven at `volts`."""
        if volts != 0.0:
            self._spent += TICK_S / self._profile.hold_s(volts)

    @property
    def exhausted(self) -> bool:
        """True once the drive has gone past the hold."""
        return self._spent > 1.0 + BUDGET_TOLERANCE

    def refill(self) -> None:
        self._spent = 0.0
