import pytest

from jack.show.motion.motors import HAT1_ADDRESS, HAT2_ADDRESS, MOTOR_NAMES, MOTORS, MotorSpec, motor_spec


def test_the_four_motors_and_where_they_are_wired():
    assert MOTORS == (
        MotorSpec("mouth", 0x40, "A", two_sided=False),
        MotorSpec("hand", 0x40, "B", two_sided=True),
        MotorSpec("pivot", 0x41, "A", two_sided=True),
        MotorSpec("elbow", 0x41, "B", two_sided=False),
    )
    assert (HAT1_ADDRESS, HAT2_ADDRESS) == (0x40, 0x41)


def test_motor_names_in_registry_order():
    assert MOTOR_NAMES == ("mouth", "hand", "pivot", "elbow")


def test_motor_spec_looks_up_by_name():
    assert motor_spec("elbow").channel == "B"


def test_unknown_motor_is_rejected_listing_the_known_ones():
    with pytest.raises(ValueError, match="mouth, hand, pivot, elbow"):
        motor_spec("tail")
