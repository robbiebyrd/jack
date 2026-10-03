import pytest

from jack.show.motion.mouth import CLOSE, OPEN_RAMP_S, RELAX, describe, hold, open_fully, ramp, rest


def test_hold_keeps_one_level():
    assert hold(-2.0, 0.5) == (-2.0, -2.0, 0.5)


def test_ramp_moves_between_levels():
    assert ramp(0.0, -6.0, 0.25) == (0.0, -6.0, 0.25)


def test_close_is_a_quarter_second_at_plus_one_volt():
    assert CLOSE == (hold(1.0, 0.25),)


def test_relax_is_half_a_second_at_minus_two_volts():
    assert RELAX == (hold(-2.0, 0.5),)


def test_open_fully_ramps_to_minus_six_volts_then_holds_for_the_given_time():
    assert OPEN_RAMP_S == 0.25
    assert open_fully(0.5) == (ramp(0.0, -6.0, 0.25), hold(-6.0, 0.5))


def test_rest_holds_zero_volts_for_the_given_time():
    assert rest(1.5) == (hold(0.0, 1.5),)


@pytest.mark.parametrize("pose", [open_fully, rest])
def test_timed_poses_need_a_positive_duration(pose):
    with pytest.raises(ValueError):
        pose(0)


def test_describe_summarises_holds_and_ramps():
    assert describe((hold(1.0, 0.25), ramp(0.0, -6.0, 0.25))) == "+1.0 V for 0.25 s, +0.0 -> -6.0 V over 0.25 s"
