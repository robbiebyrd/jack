import math

import pytest

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.lip_sync import LipSync, check_mouth_gain
from jack.show.audio.pcm import TICK_S
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.motor_driver import MotorDriver, Rest
from jack.show.motion.poses import Hold
from tests.profiles import MOUTH, profile

BRAKE = Rest("brake")
CLOSE_TICKS = round(MOUTH.rest_pulse_s / TICK_S)  # the test mouth's rest pulse: +0.5 V for 4 ticks
DEFAULT_GAIN_DB = TalkSettings().mouth_gain_db


def level_for(volts: float, gain_db: float = DEFAULT_GAIN_DB, max_v: float = MOUTH.max_v) -> float:
    """The smoothed level (dBFS) that maps to `volts` at `gain_db`."""
    return 20 * math.log10(volts / max_v) - gain_db


QUIET = level_for(0.5)  # maps below the test mouth's 1 V min_v
FULL = level_for(6.0)

# The real mouth opens with positive volts and pulls closed with negative ones (sign +1, rest pulse -0.5 V).
REAL_ORIENTATION = {
    "sign": 1, "rest_pulse_v": -0.5, "slew_v_per_s": 1000.0, "holds": (Hold(6.0, 1.0), Hold(-1.0, 1.0)),
}


def target(settings=TalkSettings(), mouth=MOUTH):
    return LipSync(settings, mouth).target


# What the voice asks of the mouth.


def test_silence_asks_for_rest():
    assert target()(FLOOR_DB) is None


def test_the_default_gain_opens_the_mouth_fully_at_minus_14_dbfs_and_louder_stays_fully_open():
    assert [target()(level) for level in (-14.0, 0.0)] == pytest.approx([-6.0, -6.0])


def test_volts_are_proportional_to_amplitude():
    assert target()(level_for(3.0)) == pytest.approx(-3.0)
    assert target()(level_for(1.5)) == pytest.approx(-1.5)
    assert target()(level_for(3.0) - 20 * math.log10(2)) == pytest.approx(-1.5)  # half the amplitude


def test_more_gain_opens_the_mouth_wider_for_the_same_sound():
    doubled = DEFAULT_GAIN_DB + 20 * math.log10(2)
    assert target(TalkSettings(mouth_gain_db=doubled))(level_for(1.5)) == pytest.approx(-3.0)


def test_just_above_min_v_opens_and_just_below_asks_for_rest():
    assert target()(level_for(1.01)) == pytest.approx(-1.01)
    assert target()(level_for(0.99)) is None


def test_volts_come_from_the_mouth_profile():
    ask = target(mouth=profile("mouth", min_v=2.0, max_v=4.0))
    assert ask(level_for(4.0, max_v=4.0)) == pytest.approx(-4.0)
    assert ask(level_for(1.5, max_v=4.0)) is None


def test_a_positive_sign_mouth_opens_with_positive_volts_in_proportion_to_amplitude():
    ask = target(mouth=profile("mouth", **REAL_ORIENTATION))
    assert [ask(level) for level in (level_for(3.0), level_for(1.5), FULL)] == pytest.approx([3.0, 1.5, 6.0])


def test_a_gain_at_which_silence_would_open_the_mouth_is_refused():
    # Silence (-90 dBFS) maps to 6 V x 10^((gain - 90) / 20): at or above the 1 V min_v from about +74.4 dB.
    check_mouth_gain(TalkSettings(mouth_gain_db=74.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        check_mouth_gain(TalkSettings(mouth_gain_db=75.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        LipSync(TalkSettings(mouth_gain_db=75.0), MOUTH)


# What the mouth does about it: the target through the mouth's MotorDriver, as the talk loop runs it in live mode.


def mouth(settings=TalkSettings(), **overrides):
    """Level in, drive out."""
    mouth_profile = profile("mouth", **overrides)
    lip_sync, driver = LipSync(settings, mouth_profile), MotorDriver(mouth_profile)
    return lambda level: driver.update(lip_sync.target(level))


def unslewed(**settings):
    """A mouth whose opening isn't slew-limited, so each test sees target voltages directly."""
    return mouth(TalkSettings(**settings), slew_v_per_s=1000.0)


def run(a_mouth, levels):
    return [a_mouth(level) for level in levels]


def test_silence_keeps_the_mouth_closed():
    assert run(unslewed(), [FLOOR_DB] * 3) == [BRAKE] * 3


def test_dropping_below_min_v_pulls_closed_then_brakes():
    drives = run(unslewed(), [level_for(3.0)] + [QUIET] * (CLOSE_TICKS + 2))
    assert drives[0] == pytest.approx(-3.0)
    assert drives[1:] == [0.5] * CLOSE_TICKS + [BRAKE, BRAKE]


def test_quiet_without_an_opening_needs_no_pull():
    assert run(unslewed(), [QUIET, QUIET]) == [BRAKE, BRAKE]


def test_sound_interrupts_the_closing_pull():
    assert run(unslewed(), [level_for(3.0), QUIET, level_for(3.0)]) == pytest.approx([-3.0, 0.5, -3.0])


def test_opening_is_slew_limited_to_48_volts_per_second():
    assert run(mouth(), [FULL] * 3) == pytest.approx([-0.96, -1.92, -2.88])


def test_an_interrupted_pull_reopens_from_zero_under_the_slew_limit():
    a_mouth = mouth()
    run(a_mouth, [FULL] * 10)
    assert run(a_mouth, [QUIET, FULL]) == pytest.approx([0.5, -0.96])


def test_easing_off_is_not_slew_limited():
    a_mouth = mouth()
    run(a_mouth, [FULL] * 20)
    assert a_mouth(level_for(2.0)) == pytest.approx(-2.0)


def budgeted():
    """An unslewed mouth that holds fully open (-6 V) for 0.4 s (20 ticks) and 1 V for 2 s."""
    return mouth(slew_v_per_s=1000.0, holds=(Hold(-6.0, 0.4), Hold(-1.0, 2.0), Hold(1.0, 1.0)))


def test_a_long_full_open_closes_when_its_hold_is_spent():
    # The spent budget rests one tick (refilling it) before the closing pull.
    drives = run(budgeted(), [FULL] * (20 + 1 + CLOSE_TICKS + 1))
    assert drives[:20] == pytest.approx([-6.0] * 20)
    assert drives[20:] == [BRAKE] + [0.5] * CLOSE_TICKS + [BRAKE]


def test_a_smaller_opening_runs_for_its_longer_hold():
    # At 1.01 V the hold interpolates to 2.0 - 1.6 x 0.01 / 5 = 1.9968 s: each tick spends 0.02 / 1.9968, so 99
    # ticks fit and the 100th (1.0016 of the budget) closes the mouth: 50x longer than at full open.
    drives = run(budgeted(), [level_for(1.01)] * 100)
    assert drives[:99] == pytest.approx([-1.01] * 99)
    assert drives[99] == BRAKE


def test_a_spent_mouth_stays_closed_through_sound_until_a_quiet_moment():
    a_mouth = budgeted()
    run(a_mouth, [FULL] * (20 + 1 + CLOSE_TICKS))  # spent, a rest tick, then the whole pull
    assert run(a_mouth, [FULL, level_for(1.5), FULL]) == [BRAKE] * 3
    assert a_mouth(QUIET) == BRAKE
    assert run(a_mouth, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_closing_between_syllables_refills_the_budget():
    a_mouth = budgeted()
    run(a_mouth, [FULL] * 19)
    # The pull spends the nearly spent budget, which rests a tick mid-pull; the rest tick after the pull refills it.
    assert run(a_mouth, [QUIET] * (CLOSE_TICKS + 2)) == [0.5, 0.5, BRAKE, 0.5, 0.5, BRAKE]
    assert run(a_mouth, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_the_pull_comes_from_the_mouth_profile():
    a_mouth = mouth(slew_v_per_s=1000.0, min_v=2.0, max_v=4.0, rest_pulse_v=0.7)
    assert a_mouth(level_for(4.0, max_v=4.0)) == pytest.approx(-4.0)
    assert a_mouth(level_for(1.5, max_v=4.0)) == 0.7


def test_a_mouth_without_a_rest_pulse_closes_straight_to_the_brake():
    a_mouth = mouth(slew_v_per_s=1000.0, rest_pulse_v=0.0, rest_pulse_s=0.0)
    assert run(a_mouth, [level_for(3.0), QUIET, QUIET]) == pytest.approx([-3.0, BRAKE, BRAKE])


def test_flicker_at_the_threshold_cannot_stack_closing_pulls_past_the_hold():
    # The test mouth holds 1 s at every voltage it uses (its -3 V opening and +0.5 V pull): 50 ticks of drive
    # between rests. Sound flickering one tick above min_v and four below would otherwise pull forever.
    drives = run(unslewed(), [level_for(3.0), QUIET, QUIET, QUIET, QUIET] * 30)
    longest_drive = current = 0
    for drive in drives:
        current = 0 if drive == BRAKE else current + 1
        longest_drive = max(longest_drive, current)
    assert longest_drive <= 50


def test_a_positive_sign_mouth_pulls_closed_with_its_negative_rest_pulse():
    drives = run(mouth(**REAL_ORIENTATION), [level_for(3.0)] + [QUIET] * (CLOSE_TICKS + 1))
    assert drives[0] == pytest.approx(3.0)
    assert drives[1:] == [-0.5] * CLOSE_TICKS + [BRAKE]


def test_a_positive_sign_mouth_is_slew_limited_upward():
    assert run(mouth(**{**REAL_ORIENTATION, "slew_v_per_s": 48.0}), [FULL] * 3) == pytest.approx([0.96, 1.92, 2.88])
