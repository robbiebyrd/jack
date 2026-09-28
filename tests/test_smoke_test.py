import pytest

from motor_test.smoke_test import run_ramp_loop
from tests.fakes import RecordingMotor


class LoopEnded(Exception):
    pass


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


def test_sets_forward_before_driving():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 10], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[0] == ("forward",)
    assert motor.calls[1] == ("duty", 0)


def test_plays_counts_in_order_and_repeats_the_cycle():
    motor = RecordingMotor()
    counts = [0, 5, 9, 5]
    sleep, _ = sleep_that_raises_after(len(counts) * 2)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, counts, step_s=0.04, sleep=sleep, on_cycle=no_op)
    duties = [call[1] for call in motor.calls if call[0] == "duty"]
    assert duties == counts * 2


def test_waits_one_step_after_each_count():
    motor = RecordingMotor()
    sleep, slept = sleep_that_raises_after(6)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert slept == [0.04] * 6


def test_stops_motor_when_loop_is_interrupted_by_an_error():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(3)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[-1] == ("stop",)


def test_stops_motor_on_system_exit_from_sigterm():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(2, exception=SystemExit(0))
    with pytest.raises(SystemExit):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[-1] == ("stop",)


def test_on_cycle_runs_once_after_each_completed_cycle():
    motor = RecordingMotor()
    events = []

    def sleep(seconds):
        events.append("step")
        if len(events) >= 7:  # 2 full cycles of 3 steps, plus 1 on_cycle marker per cycle
            raise LoopEnded

    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=lambda: events.append("cycle"))
    assert events == ["step", "step", "step", "cycle", "step", "step", "step"]


def test_empty_cycle_is_rejected_without_touching_the_motor():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(ValueError):
        run_ramp_loop(motor, [], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls == []
