import pytest

from motor_test.mouth import CLOSE, RELAX, open_fully, rest


def test_close_is_a_quarter_second_at_plus_one_volt():
    assert CLOSE == ((1.0, 0.25),)


def test_relax_is_half_a_second_at_minus_two_volts():
    assert RELAX == ((-2.0, 0.5),)


def test_open_fully_holds_minus_six_volts_for_the_given_time():
    assert open_fully(0.5) == ((-6.0, 0.5),)


def test_rest_holds_zero_volts_for_the_given_time():
    assert rest(1.5) == ((0.0, 1.5),)


@pytest.mark.parametrize("pose", [open_fully, rest])
def test_timed_poses_need_a_positive_duration(pose):
    with pytest.raises(ValueError):
        pose(0)
