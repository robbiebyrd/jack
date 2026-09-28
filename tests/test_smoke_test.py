import pytest

from motor_test.smoke_test import run_profiles_loop
from tests.fakes import RecordingMotor


class LoopEnded(Exception):
    pass


class MotorThatFailsToStop(RecordingMotor):
    def stop(self):
        super().stop()
        raise OSError("I2C write failed")


def no_op():
    pass


def sleep_that_raises_after(n, exception=LoopEnded):
    """Return a sleep function and its log. The nth call raises `exception`."""
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) >= n:
            raise exception

    return sleep, slept


def drives(motor):
    return [call[1] for call in motor.calls if call[0] == "drive"]


def test_each_motor_plays_its_own_profile_in_step_and_repeats():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    sleep, _ = sleep_that_raises_after(8)
    with pytest.raises(LoopEnded):
        run_profiles_loop(
            [(motor_a, [0, 5, 9, 5]), (motor_b, [3, 3, -3, -3])],
            step_s=0.04,
            sleep=sleep,
            on_cycle=no_op,
        )
    assert drives(motor_a) == [0, 5, 9, 5] * 2
    assert drives(motor_b) == [3, 3, -3, -3] * 2


def test_every_motor_gets_its_step_before_the_step_wait():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    events = []

    def sleep(seconds):
        events.append(("sleep", len(motor_a.calls), len(motor_b.calls)))
        raise LoopEnded

    with pytest.raises(LoopEnded):
        run_profiles_loop([(motor_a, [1]), (motor_b, [2])], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert events == [("sleep", 1, 1)]


def test_waits_one_step_after_each_step():
    motor = RecordingMotor()
    sleep, slept = sleep_that_raises_after(6)
    with pytest.raises(LoopEnded):
        run_profiles_loop([(motor, [0, 5, 9])], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert slept == [0.04] * 6


def test_stops_every_motor_when_the_loop_is_interrupted_by_an_error():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    sleep, _ = sleep_that_raises_after(3)
    with pytest.raises(LoopEnded):
        run_profiles_loop([(motor_a, [0, 5]), (motor_b, [3, -3])], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor_a.calls[-1] == ("stop",)
    assert motor_b.calls[-1] == ("stop",)


def test_stops_every_motor_on_system_exit_from_sigterm():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    sleep, _ = sleep_that_raises_after(2, exception=SystemExit(0))
    with pytest.raises(SystemExit):
        run_profiles_loop([(motor_a, [0, 5]), (motor_b, [3, -3])], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor_a.calls[-1] == ("stop",)
    assert motor_b.calls[-1] == ("stop",)


def test_later_motors_still_stop_when_an_earlier_stop_fails():
    failing, healthy = MotorThatFailsToStop(), RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(OSError, match="I2C write failed"):
        run_profiles_loop([(failing, [0]), (healthy, [0])], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert healthy.calls[-1] == ("stop",)


def test_on_cycle_runs_once_after_each_completed_cycle():
    motor = RecordingMotor()
    events = []

    def sleep(seconds):
        events.append("step")
        if len(events) >= 7:  # 2 full cycles of 3 steps, plus 1 on_cycle marker per cycle
            raise LoopEnded

    with pytest.raises(LoopEnded):
        run_profiles_loop([(motor, [0, 5, 9])], step_s=0.04, sleep=sleep, on_cycle=lambda: events.append("cycle"))
    assert events == ["step", "step", "step", "cycle", "step", "step", "step"]


@pytest.mark.parametrize(
    "profiles",
    [
        [],  # no motors
        [(RecordingMotor(), [])],  # empty profile
        [(RecordingMotor(), [0, 1]), (RecordingMotor(), [0])],  # profiles of different lengths
    ],
)
def test_unplayable_profiles_are_rejected_without_touching_any_motor(profiles):
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(ValueError):
        run_profiles_loop(profiles, step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert all(motor.calls == [] for motor, _ in profiles)
