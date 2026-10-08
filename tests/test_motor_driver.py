import pytest

from jack.show.motion.motor_driver import MotorDriver, Rest
from jack.show.motion.poses import Hold
from tests.profiles import profile

BRAKE = Rest("brake")


def run(driver, targets):
    return [driver.update(target) for target in targets]


def test_no_command_rests_braked():
    assert run(MotorDriver(profile("hand")), [None, None]) == [BRAKE, BRAKE]


def test_coast_profile_rests_coasting():
    assert MotorDriver(profile("hand", rest="coast")).update(None) == Rest("coast")


def test_zero_volts_means_rest():
    assert MotorDriver(profile("hand")).update(0.0) == BRAKE


def test_driving_harder_is_slew_limited():
    # 24 V/s x 0.02 s = 0.48 V per tick.
    assert run(MotorDriver(profile("hand")), [2.0] * 5) == pytest.approx([0.48, 0.96, 1.44, 1.92, 2.0])


def test_easing_off_is_not_slew_limited():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))
    assert run(driver, [2.0, 1.0]) == [2.0, 1.0]


def test_reversing_slews_through_zero():
    driver = MotorDriver(profile("pivot", slew_v_per_s=50.0))  # 1 V per tick
    assert run(driver, [2.0, 2.0, -2.0, -2.0, -2.0, -2.0]) == pytest.approx([1.0, 2.0, 1.0, 0.0, -1.0, -2.0])


def test_max_hold_trips_then_rests_and_stays_rested_while_commands_continue():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))  # max_hold 1.0 s = 50 ticks
    held = run(driver, [2.0] * 50)
    assert held == [2.0] * 50 and not driver.max_hold_tripped
    assert run(driver, [2.0] * 3) == [BRAKE] * 3
    assert driver.max_hold_tripped


def test_max_hold_resets_once_commands_stop():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))
    run(driver, [2.0] * 51)
    driver.update(None)
    assert not driver.max_hold_tripped
    assert driver.update(2.0) == 2.0


def test_rest_pulse_plays_on_the_way_to_rest():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))  # rest pulse +0.5 V for 0.08 s = 4 ticks
    assert run(driver, [-3.0, None, None, None, None, None]) == [-3.0, 0.5, 0.5, 0.5, 0.5, BRAKE]


def test_no_pulse_when_already_resting():
    assert run(MotorDriver(profile("mouth")), [None, None]) == [BRAKE, BRAKE]


def test_new_command_interrupts_the_rest_pulse():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    assert run(driver, [-3.0, None, -2.0]) == [-3.0, 0.5, -2.0]


def test_tripping_max_hold_rests_a_tick_then_plays_the_rest_pulse():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    run(driver, [-3.0] * 50)
    # The tick that spends the budget rests (refilling it), then the pulse plays on the refilled budget.
    assert run(driver, [-3.0] * 6) == [BRAKE, 0.5, 0.5, 0.5, 0.5, BRAKE]


def test_the_rest_pulse_spends_the_budget_and_only_a_rest_tick_refills_it():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))  # every hold 1 s: 0.02 a tick
    run(driver, [-3.0] * 49)
    # The second pulse tick overspends: a rest tick refills, then the pulse finishes.
    assert run(driver, [None] * 6) == [0.5, BRAKE, 0.5, 0.5, 0.5, BRAKE]


def test_a_command_flickering_on_and_off_cannot_stack_pulses_past_the_hold():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    drives = run(driver, [-3.0, None, None, None, None] * 30)
    longest_drive = current = 0
    for drive in drives:
        current = 0 if drive == BRAKE else current + 1
        longest_drive = max(longest_drive, current)
    assert longest_drive <= 50


def test_a_reversal_landing_on_zero_then_resting_restarts_the_hold_count():
    driver = MotorDriver(profile("pivot", slew_v_per_s=50.0))  # 1 V per tick; max_hold 1.0 s = 50 ticks
    assert run(driver, [1.0, -1.0, None]) == [1.0, 0.0, BRAKE]
    assert run(driver, [2.0] * 50)[-1] == 2.0 and not driver.max_hold_tripped
    assert driver.update(2.0) == BRAKE and driver.max_hold_tripped


def budget_profile():
    # 6 V lasts 0.4 s (20 ticks, 0.05 a tick); 2 V lasts 2 s (100 ticks, 0.01 a tick).
    return profile("hand", slew_v_per_s=1000.0, max_v=6.0, holds=(Hold(2.0, 2.0), Hold(6.0, 0.4), Hold(-2.0, 1.0)))


def test_a_steady_high_voltage_trips_at_its_hold():
    driver = MotorDriver(budget_profile())
    assert run(driver, [6.0] * 20) == [6.0] * 20 and not driver.max_hold_tripped
    assert driver.update(6.0) == BRAKE and driver.max_hold_tripped


def test_a_steady_low_voltage_runs_for_its_longer_hold():
    driver = MotorDriver(budget_profile())
    assert run(driver, [2.0] * 100) == [2.0] * 100 and not driver.max_hold_tripped
    assert driver.update(2.0) == BRAKE and driver.max_hold_tripped


def test_mixed_voltages_share_one_budget():
    driver = MotorDriver(budget_profile())
    run(driver, [6.0] * 10)  # half the budget
    assert run(driver, [2.0] * 50) == [2.0] * 50 and not driver.max_hold_tripped  # the other half
    assert driver.update(2.0) == BRAKE and driver.max_hold_tripped


def test_any_rest_refills_the_budget():
    driver = MotorDriver(budget_profile())
    run(driver, [6.0] * 19)
    driver.update(None)
    assert run(driver, [6.0] * 20) == [6.0] * 20 and not driver.max_hold_tripped


def test_a_long_pose_is_stopped_at_the_hold_not_its_requested_length():
    driver = MotorDriver(budget_profile())
    assert run(driver, [6.0] * 30).count(6.0) == 20 and driver.max_hold_tripped


def test_slewing_through_zero_spends_no_budget():
    # 1 V per tick; every hold is 1 s, so each tick at nonzero volts spends 0.02 and 50 such ticks spend it all.
    driver = MotorDriver(profile("pivot", slew_v_per_s=50.0))
    assert run(driver, [1.0, -1.0]) == [1.0, 0.0]  # 1 nonzero tick spent; the 0 V tick spends nothing
    held = run(driver, [-2.0] * 49)  # -1 V, then 48 at -2 V: 49 more, 50 in all
    assert held[0] == -1.0 and held[-1] == -2.0 and not driver.max_hold_tripped
    assert driver.update(-2.0) == BRAKE and driver.max_hold_tripped
