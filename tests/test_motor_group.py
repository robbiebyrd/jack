import pytest

from motor_test.motor_group import MotorGroup
from tests.fakes import RecordingMotor


class MotorThatFailsToStop(RecordingMotor):
    def stop(self):
        super().stop()
        raise OSError("I2C write failed")


def test_commands_reach_every_motor_in_order():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    group = MotorGroup([motor_a, motor_b])
    group.set_forward()
    group.set_duty(2048)
    group.stop()
    expected = [("forward",), ("duty", 2048), ("stop",)]
    assert motor_a.calls == expected
    assert motor_b.calls == expected


def test_stop_still_brakes_later_motors_when_one_fails():
    failing, healthy = MotorThatFailsToStop(), RecordingMotor()
    group = MotorGroup([failing, healthy])
    with pytest.raises(OSError, match="I2C write failed"):
        group.stop()
    assert healthy.calls == [("stop",)]


def test_empty_group_is_rejected():
    with pytest.raises(ValueError):
        MotorGroup([])
