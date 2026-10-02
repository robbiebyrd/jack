import pytest

from motor_test.motor_driver import MotorDriver, Rest
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
    # 24 V/s × 0.02 s = 0.48 V per tick.
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


def test_tripping_max_hold_plays_the_rest_pulse():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    run(driver, [-3.0] * 50)
    assert run(driver, [-3.0] * 5) == [0.5, 0.5, 0.5, 0.5, BRAKE]
