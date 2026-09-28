import pytest

from motor_test.lip_sync import MouthController
from motor_test.talk_settings import TalkSettings

QUIET = -60.0
GATE = -35.0  # default gate_open_db
BETWEEN_GATES = -38.0  # between gate_close_db (-40) and gate_open_db
BELOW_CLOSE = -45.0
FULL = -10.0  # default full_db


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(open_slew_v_per_s=1000.0, **overrides))


def run(controller, levels):
    return [controller.update(level) for level in levels]


def test_mouth_stays_closed_while_quiet():
    assert run(unslewed(), [QUIET] * 3) == [0.0, 0.0, 0.0]


def test_reaching_the_gate_opens_to_relaxed_open():
    assert unslewed().update(GATE) == -2.0


def test_full_level_opens_fully_and_louder_stays_at_fully_open():
    assert run(unslewed(), [FULL, 0.0]) == [-6.0, -6.0]


def test_opening_is_proportional_to_loudness_between_gate_and_full():
    assert unslewed().update(-22.5) == pytest.approx(-4.0)


def test_between_the_gates_an_open_mouth_stays_open():
    assert run(unslewed(), [GATE, BETWEEN_GATES]) == [-2.0, -2.0]


def test_between_the_gates_a_closed_mouth_stays_closed():
    assert unslewed().update(BETWEEN_GATES) == 0.0


def test_falling_below_the_close_gate_pulses_closed_then_rests():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [GATE] + [BELOW_CLOSE] * (settings.close_ticks + 2))
    assert volts == [-2.0] + [0.5] * settings.close_ticks + [0.0, 0.0]


def test_next_syllable_interrupts_the_close_pulse():
    assert run(unslewed(), [GATE, BELOW_CLOSE, GATE]) == [-2.0, 0.5, -2.0]


def test_opening_is_slew_limited_to_24_volts_per_second():
    controller = MouthController(TalkSettings())
    assert run(controller, [FULL] * 3) == pytest.approx([-0.48, -0.96, -1.44])


def test_closing_down_to_a_smaller_opening_is_not_slew_limited():
    controller = MouthController(TalkSettings())
    run(controller, [FULL] * 20)
    assert controller.update(GATE) == -2.0


def test_stall_guard_caps_a_long_full_open_at_relaxed_open():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [FULL] * (settings.max_stall_ticks + 2))
    assert volts == [-6.0] * settings.max_stall_ticks + [-2.0, -2.0]


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
