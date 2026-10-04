import math

import pytest

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.lip_sync import MouthController, check_mouth_gain
from jack.show.audio.pcm import TICK_S
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.poses import Hold
from tests.profiles import MOUTH, profile

CLOSE_TICKS = round(MOUTH.rest_pulse_s / TICK_S)  # the test mouth's rest pulse: +0.5 V for 4 ticks
DEFAULT_GAIN_DB = TalkSettings().mouth_gain_db


def level_for(volts: float, gain_db: float = DEFAULT_GAIN_DB, max_v: float = MOUTH.max_v) -> float:
    """The smoothed level (dBFS) that maps to `volts` at `gain_db`."""
    return 20 * math.log10(volts / max_v) - gain_db


QUIET = level_for(0.5)  # maps below the test mouth's 1 V min_v
FULL = level_for(6.0)


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(**overrides), profile("mouth", slew_v_per_s=1000.0))


def run(controller, levels):
    return [controller.update(level) for level in levels]


def test_silence_keeps_the_mouth_closed():
    assert run(unslewed(), [FLOOR_DB] * 3) == [0.0, 0.0, 0.0]


def test_the_default_gain_opens_the_mouth_fully_at_minus_14_dbfs_and_louder_stays_fully_open():
    assert run(unslewed(), [-14.0, 0.0]) == pytest.approx([-6.0, -6.0])


def test_volts_are_proportional_to_amplitude():
    controller = unslewed()
    assert controller.update(level_for(3.0)) == pytest.approx(-3.0)
    assert controller.update(level_for(1.5)) == pytest.approx(-1.5)
    assert controller.update(level_for(3.0) - 20 * math.log10(2)) == pytest.approx(-1.5)  # half the amplitude


def test_more_gain_opens_the_mouth_wider_for_the_same_sound():
    doubled = DEFAULT_GAIN_DB + 20 * math.log10(2)
    assert unslewed(mouth_gain_db=doubled).update(level_for(1.5)) == pytest.approx(-3.0)


def test_just_above_min_v_opens_and_just_below_does_not():
    assert unslewed().update(level_for(1.01)) == pytest.approx(-1.01)
    assert unslewed().update(level_for(0.99)) == 0.0


def test_dropping_below_min_v_pulls_closed_then_brakes():
    volts = run(unslewed(), [level_for(3.0)] + [QUIET] * (CLOSE_TICKS + 2))
    assert volts == pytest.approx([-3.0] + [0.5] * CLOSE_TICKS + [0.0, 0.0])


def test_quiet_without_an_opening_needs_no_pull():
    assert run(unslewed(), [QUIET, QUIET]) == [0.0, 0.0]


def test_sound_interrupts_the_closing_pull():
    assert run(unslewed(), [level_for(3.0), QUIET, level_for(3.0)]) == pytest.approx([-3.0, 0.5, -3.0])


def test_opening_is_slew_limited_to_48_volts_per_second():
    controller = MouthController(TalkSettings(), MOUTH)
    assert run(controller, [FULL] * 3) == pytest.approx([-0.96, -1.92, -2.88])


def test_an_interrupted_pull_reopens_from_zero_under_the_slew_limit():
    controller = MouthController(TalkSettings(), MOUTH)
    run(controller, [FULL] * 10)
    assert run(controller, [QUIET, FULL]) == pytest.approx([0.5, -0.96])


def test_easing_off_is_not_slew_limited():
    controller = MouthController(TalkSettings(), MOUTH)
    run(controller, [FULL] * 20)
    assert controller.update(level_for(2.0)) == pytest.approx(-2.0)


def budgeted():
    """An unslewed controller whose mouth holds fully open (-6 V) for 0.4 s (20 ticks) and 1 V for 2 s."""
    holds = (Hold(-6.0, 0.4), Hold(-1.0, 2.0), Hold(1.0, 1.0))
    return MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, holds=holds))


def test_a_long_full_open_closes_when_its_hold_is_spent():
    # The spent budget rests one 0 V tick (refilling it) before the closing pull.
    volts = run(budgeted(), [FULL] * (20 + 1 + CLOSE_TICKS + 1))
    assert volts == pytest.approx([-6.0] * 20 + [0.0] + [0.5] * CLOSE_TICKS + [0.0])


def test_a_smaller_opening_runs_for_its_longer_hold():
    # At 1.01 V the hold interpolates to 2.0 - 1.6 x 0.01 / 5 = 1.9968 s: each tick spends 0.02 / 1.9968, so 99
    # ticks fit and the 100th (1.0016 of the budget) closes the mouth: 50x longer than at full open.
    volts = run(budgeted(), [level_for(1.01)] * 100)
    assert volts == pytest.approx([-1.01] * 99 + [0.0])


def test_a_spent_mouth_stays_closed_through_sound_until_a_quiet_moment():
    controller = budgeted()
    run(controller, [FULL] * (20 + 1 + CLOSE_TICKS))  # spent, a 0 V rest, then the whole pull
    assert run(controller, [FULL, level_for(1.5), FULL]) == [0.0, 0.0, 0.0]
    assert controller.update(QUIET) == 0.0
    assert run(controller, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_closing_between_syllables_refills_the_budget():
    controller = budgeted()
    run(controller, [FULL] * 19)
    # The pull spends the nearly spent budget, which rests a tick mid-pull; the 0 V tick after the pull refills it.
    assert run(controller, [QUIET] * (CLOSE_TICKS + 2)) == pytest.approx([0.5, 0.5, 0.0, 0.5, 0.5, 0.0])
    assert run(controller, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_volts_and_the_pull_come_from_the_mouth_profile():
    mouth = profile("mouth", slew_v_per_s=1000.0, min_v=2.0, max_v=4.0, rest_pulse_v=0.7)
    controller = MouthController(TalkSettings(), mouth)
    assert controller.update(level_for(4.0, max_v=4.0)) == pytest.approx(-4.0)
    assert controller.update(level_for(1.5, max_v=4.0)) == 0.7


def test_a_mouth_without_a_rest_pulse_closes_straight_to_zero():
    mouth = profile("mouth", slew_v_per_s=1000.0, rest_pulse_v=0.0, rest_pulse_s=0.0)
    controller = MouthController(TalkSettings(), mouth)
    assert run(controller, [level_for(3.0), QUIET, QUIET]) == pytest.approx([-3.0, 0.0, 0.0])


def test_a_gain_at_which_silence_would_open_the_mouth_is_refused():
    # Silence (-90 dBFS) maps to 6 V x 10^((gain - 90) / 20): at or above the 1 V min_v from about +74.4 dB.
    check_mouth_gain(TalkSettings(mouth_gain_db=74.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        check_mouth_gain(TalkSettings(mouth_gain_db=75.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        MouthController(TalkSettings(mouth_gain_db=75.0), MOUTH)


def test_flicker_at_the_threshold_cannot_stack_closing_pulls_past_the_hold():
    # The test mouth holds 1 s at every voltage it uses (its -3 V opening and +0.5 V pull): 50 ticks of drive
    # between 0 V rests. Sound flickering one tick above min_v and four below would otherwise pull forever.
    volts = run(unslewed(), [level_for(3.0), QUIET, QUIET, QUIET, QUIET] * 30)
    longest_drive = current = 0
    for v in volts:
        current = current + 1 if v != 0.0 else 0
        longest_drive = max(longest_drive, current)
    assert longest_drive <= 50


# The real mouth opens with positive volts and pulls closed with negative ones (sign +1, rest pulse -0.5 V).
REAL_ORIENTATION = dict(
    sign=1, rest_pulse_v=-0.5, slew_v_per_s=1000.0, holds=(Hold(6.0, 1.0), Hold(-1.0, 1.0)),
)


def test_a_positive_sign_mouth_opens_with_positive_volts_in_proportion_to_amplitude():
    controller = MouthController(TalkSettings(), profile("mouth", **REAL_ORIENTATION))
    assert run(controller, [level_for(3.0), level_for(1.5), FULL]) == pytest.approx([3.0, 1.5, 6.0])


def test_a_positive_sign_mouth_pulls_closed_with_its_negative_rest_pulse():
    controller = MouthController(TalkSettings(), profile("mouth", **REAL_ORIENTATION))
    volts = run(controller, [level_for(3.0)] + [QUIET] * (CLOSE_TICKS + 1))
    assert volts == pytest.approx([3.0] + [-0.5] * CLOSE_TICKS + [0.0])


def test_a_positive_sign_mouth_is_slew_limited_upward():
    controller = MouthController(TalkSettings(), profile("mouth", **{**REAL_ORIENTATION, "slew_v_per_s": 48.0}))
    assert run(controller, [FULL] * 3) == pytest.approx([0.96, 1.92, 2.88])
