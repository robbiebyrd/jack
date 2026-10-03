import pytest

from jack.show.audio.lip_sync import MouthController
from jack.show.audio.pcm import TICK_S
from jack.show.motion.poses import Hold
from jack.show.audio.talk_settings import TalkSettings
from tests.profiles import MOUTH, profile

_DEFAULTS = TalkSettings()
GATE = _DEFAULTS.gate_open_db
BETWEEN_GATES = (_DEFAULTS.gate_close_db + _DEFAULTS.gate_open_db) / 2
BELOW_CLOSE = _DEFAULTS.gate_close_db - 5
QUIET = _DEFAULTS.gate_close_db - 20
FULL = _DEFAULTS.full_db
HALF_LOUD = (_DEFAULTS.gate_open_db + _DEFAULTS.full_db) / 2
CLOSE_TICKS = round(MOUTH.rest_pulse_s / TICK_S)


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(**overrides), profile("mouth", slew_v_per_s=1000.0))


def run(controller, levels):
    return [controller.update(level) for level in levels]


def test_mouth_stays_closed_while_quiet():
    assert run(unslewed(), [QUIET] * 3) == [0.0, 0.0, 0.0]


def test_reaching_the_gate_opens_to_relaxed_open():
    assert unslewed().update(GATE) == -1.0


def test_full_level_opens_fully_and_louder_stays_at_fully_open():
    assert run(unslewed(), [FULL, 0.0]) == [-6.0, -6.0]


def test_opening_is_proportional_to_loudness_between_gate_and_full():
    assert unslewed(open_curve=1.0).update(HALF_LOUD) == pytest.approx(-3.5)


def test_curve_opens_a_half_loud_syllable_a_quarter_of_the_way():
    assert unslewed().update(HALF_LOUD) == pytest.approx(-2.25)


def test_curve_of_one_is_linear():
    assert unslewed(open_curve=1.0).update(HALF_LOUD) == pytest.approx(-3.5)


def test_between_the_gates_an_open_mouth_stays_open():
    assert run(unslewed(), [GATE, BETWEEN_GATES]) == [-1.0, -1.0]


def test_between_the_gates_a_closed_mouth_stays_closed():
    assert unslewed().update(BETWEEN_GATES) == 0.0


def test_falling_below_the_close_gate_pulses_closed_then_rests():
    volts = run(unslewed(), [GATE] + [BELOW_CLOSE] * (CLOSE_TICKS + 2))
    assert volts == [-1.0] + [0.5] * CLOSE_TICKS + [0.0, 0.0]


def test_next_syllable_interrupts_the_close_pulse():
    assert run(unslewed(), [GATE, BELOW_CLOSE, GATE]) == [-1.0, 0.5, -1.0]


def test_opening_is_slew_limited_to_48_volts_per_second():
    controller = MouthController(TalkSettings(), MOUTH)
    assert run(controller, [FULL] * 3) == pytest.approx([-0.96, -1.92, -2.88])


def test_closing_down_to_a_smaller_opening_is_not_slew_limited():
    controller = MouthController(TalkSettings(), MOUTH)
    run(controller, [FULL] * 20)
    assert controller.update(GATE) == -1.0


def budgeted():
    """An unslewed controller whose mouth holds fully open (-6 V) for 0.4 s (20 ticks) and relaxed open (-1 V) for 2 s."""
    holds = (Hold(-6.0, 0.4), Hold(-1.0, 2.0), Hold(1.0, 1.0))
    return MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, holds=holds))


def test_a_long_full_open_closes_when_its_hold_is_spent():
    volts = run(budgeted(), [FULL] * (20 + CLOSE_TICKS + 1))
    assert volts == [-6.0] * 20 + [0.5] * CLOSE_TICKS + [0.0]


def test_a_smaller_opening_runs_for_its_longer_hold():
    volts = run(budgeted(), [GATE] * 101)
    assert volts == [-1.0] * 100 + [0.5]


def test_a_spent_mouth_stays_closed_through_loud_speech_until_a_pause():
    controller = budgeted()
    run(controller, [FULL] * (20 + CLOSE_TICKS))
    assert run(controller, [FULL, BETWEEN_GATES, FULL]) == [0.0, 0.0, 0.0]
    assert controller.update(BELOW_CLOSE) == 0.0
    assert run(controller, [FULL] * 20) == [-6.0] * 20


def test_closing_between_syllables_refills_the_budget():
    controller = budgeted()
    run(controller, [FULL] * 19)
    run(controller, [BELOW_CLOSE] * (CLOSE_TICKS + 1))
    assert run(controller, [FULL] * 20) == [-6.0] * 20


def test_volts_and_close_pulse_come_from_the_mouth_profile():
    controller = MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, min_v=2.0, max_v=4.0, rest_pulse_v=0.7))
    assert controller.update(GATE) == -2.0
    assert controller.update(FULL) == -4.0
    assert controller.update(BELOW_CLOSE) == 0.7


def test_a_mouth_without_a_rest_pulse_closes_straight_to_zero():
    controller = MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, rest_pulse_v=0.0, rest_pulse_s=0.0))
    assert [controller.update(level) for level in (GATE, BELOW_CLOSE, BELOW_CLOSE)] == [-1.0, 0.0, 0.0]
