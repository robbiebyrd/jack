import pytest

from motor_test.lip_sync import MouthController
from motor_test.talk_settings import TalkSettings

_DEFAULTS = TalkSettings()
GATE = _DEFAULTS.gate_open_db
BETWEEN_GATES = (_DEFAULTS.gate_close_db + _DEFAULTS.gate_open_db) / 2
BELOW_CLOSE = _DEFAULTS.gate_close_db - 5
QUIET = _DEFAULTS.gate_close_db - 20
FULL = _DEFAULTS.full_db
HALF_LOUD = (_DEFAULTS.gate_open_db + _DEFAULTS.full_db) / 2


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(open_slew_v_per_s=1000.0, **overrides))


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
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [GATE] + [BELOW_CLOSE] * (settings.close_ticks + 2))
    assert volts == [-1.0] + [0.5] * settings.close_ticks + [0.0, 0.0]


def test_next_syllable_interrupts_the_close_pulse():
    assert run(unslewed(), [GATE, BELOW_CLOSE, GATE]) == [-1.0, 0.5, -1.0]


def test_opening_is_slew_limited_to_48_volts_per_second():
    controller = MouthController(TalkSettings())
    assert run(controller, [FULL] * 3) == pytest.approx([-0.96, -1.92, -2.88])


def test_closing_down_to_a_smaller_opening_is_not_slew_limited():
    controller = MouthController(TalkSettings())
    run(controller, [FULL] * 20)
    assert controller.update(GATE) == -1.0


def test_stall_guard_caps_a_long_full_open_at_relaxed_open():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [FULL] * (settings.max_stall_ticks + 2))
    assert volts == [-6.0] * settings.max_stall_ticks + [-1.0, -1.0]


def test_stall_guard_resets_after_the_mouth_closes():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    controller = MouthController(settings)
    run(controller, [FULL] * (settings.max_stall_ticks + 1))
    run(controller, [BELOW_CLOSE] * (settings.close_ticks + 1))
    assert controller.update(FULL) == -6.0


def test_a_break_below_the_stall_voltage_restarts_the_stall_timer():
    controller = unslewed()
    run(controller, [FULL] * 20)
    controller.update(GATE)
    assert run(controller, [FULL] * 20) == [-6.0] * 20
