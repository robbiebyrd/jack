from motor_test.poses import Hold
from motor_test.stall_budget import StallBudget
from tests.profiles import profile

# 6 V lasts 0.4 s (20 ticks, 0.05 a tick); 2 V lasts 2 s (100 ticks, 0.01 a tick).
HOLDS = profile("hand", max_v=6.0, holds=(Hold(2.0, 2.0), Hold(6.0, 0.4), Hold(-2.0, 1.0)))


def spend(budget, volts, ticks):
    for _ in range(ticks):
        budget.spend(volts)


def test_a_fresh_budget_is_not_exhausted():
    assert not StallBudget(HOLDS).exhausted


def test_the_hold_at_a_voltage_spends_exactly_the_whole_budget():
    budget = StallBudget(HOLDS)
    spend(budget, 6.0, 20)
    assert not budget.exhausted
    budget.spend(6.0)
    assert budget.exhausted


def test_different_voltages_share_one_budget():
    budget = StallBudget(HOLDS)
    spend(budget, 6.0, 10)
    spend(budget, 2.0, 50)
    assert not budget.exhausted
    budget.spend(2.0)
    assert budget.exhausted


def test_zero_volts_spends_nothing():
    budget = StallBudget(HOLDS)
    spend(budget, 0.0, 1000)
    spend(budget, 6.0, 20)
    assert not budget.exhausted


def test_refill_restores_the_whole_budget():
    budget = StallBudget(HOLDS)
    spend(budget, 6.0, 21)
    budget.refill()
    assert not budget.exhausted
    spend(budget, 6.0, 20)
    assert not budget.exhausted
