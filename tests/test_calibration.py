import pytest

from jack.application.calibration import MAX_MOVE_S, parse_command, pose_segments, run_calibration
from jack.show.motion.poses import Pose
from jack.show.motion.segments import hold, ramp
from tests.fakes import RecordingMotor, drives
from tests.profiles import MOUTH, profile

SUPPLY = 12.0
STEP_S = 0.05


class MotorThatFailsToDrive(RecordingMotor):
    def drive(self, count):
        super().drive(count)
        raise OSError("I2C write failed")


def scripted_input(*lines, then=EOFError):
    """read_line that returns `lines` in order, then raises `then`."""
    remaining = list(lines)

    def read_line():
        if remaining:
            return remaining.pop(0)
        raise then

    return read_line


def run(motor, *lines, then=EOFError, motor_profile=MOUTH):
    output, slept = [], []
    run_calibration(
        motor, "mouth", motor_profile, SUPPLY, STEP_S, scripted_input(*lines, then=then), output.append, slept.append
    )
    return output, slept


def test_parses_volts_and_seconds_as_a_hold():
    assert parse_command("-4.5 0.8", SUPPLY, MOUTH, STEP_S) == (hold(-4.5, 0.8),)


def test_a_pose_name_ramps_at_the_motors_slew_then_holds_for_its_default_seconds():
    # mouth open: -6 V at 48 V/s = 0.125 s, rounded up to whole 0.05 s steps = 0.15 s.
    assert parse_command("open", SUPPLY, MOUTH, STEP_S) == (ramp(0.0, -6.0, 3 * STEP_S), hold(-6.0, 0.5))


def test_a_pose_name_with_seconds_holds_that_long():
    assert parse_command("relax 0.8", SUPPLY, MOUTH, STEP_S) == (ramp(0.0, -2.0, 0.05), hold(-2.0, 0.8))


def test_pose_segments_ramp_at_least_one_step():
    assert pose_segments(MOUTH.poses["close"], 0.25, 48.0, STEP_S) == (ramp(0.0, 1.0, 0.05), hold(1.0, 0.25))


def test_pose_segments_ramp_an_exact_multiple_of_steps_without_gaining_one():
    # 2.4 V at 24 V/s = 0.1 s = exactly 2 steps (floating point gives 2.0000000000000004).
    assert pose_segments(Pose(2.4, 0.5), 0.5, 24.0, STEP_S) == (ramp(0.0, 2.4, 0.1), hold(2.4, 0.5))


def test_poses_come_from_the_motors_profile():
    elbow = profile("elbow")
    assert parse_command("up", SUPPLY, elbow, STEP_S)[-1] == hold(2.0, 0.5)
    with pytest.raises(ValueError, match="up"):
        parse_command("open", SUPPLY, elbow, STEP_S)


@pytest.mark.parametrize("line", ["q", "quit", "  q  "])
def test_q_means_quit(line):
    assert parse_command(line, SUPPLY, MOUTH, STEP_S) is None


@pytest.mark.parametrize(
    "line",
    [
        "-4.5",  # missing duration
        "-4.5 0.8 1",  # extra word
        "wide 0.8",  # not a number or a command
        "-13 0.8",  # beyond -supply
        "13 0.8",  # beyond +supply
        "nan 0.8",  # not a real voltage
        "-4.5 0",  # zero duration
        "-4.5 -1",  # negative duration
        f"-4.5 {MAX_MOVE_S + 0.1}",  # longer than the stall-safety cap
        f"open {MAX_MOVE_S + 0.1}",  # pose held longer than the stall-safety cap
        "open 0",  # zero pose duration
        "close 1 2",  # extra word after a pose
    ],
)
def test_bad_commands_are_rejected(line):
    with pytest.raises(ValueError):
        parse_command(line, SUPPLY, MOUTH, STEP_S)


def test_move_drives_step_by_step_for_the_duration_then_brakes():
    motor = RecordingMotor()
    output, slept = run(motor, "-6 0.5")
    assert motor.calls[:11] == [("drive", -2048)] * 10 + [("stop",)]
    assert slept == [STEP_S] * 10
    assert output == ["moved mouth -6.0 V for 0.5 s, braked"]


def test_duration_that_is_not_whole_steps_is_rejected_without_moving():
    motor = RecordingMotor()
    output, slept = run(motor, "-4.5 0.33")
    assert drives(motor) == []
    assert slept == []
    assert len(output) == 1 and "steps" in output[0]


def test_bad_line_reports_the_problem_without_moving_the_motor():
    motor = RecordingMotor()
    output, slept = run(motor, "-20 1")
    assert not any(call[0] == "drive" for call in motor.calls)
    assert slept == []
    assert len(output) == 1 and "between" in output[0]


def test_blank_lines_are_ignored():
    motor = RecordingMotor()
    output, _ = run(motor, "", "   ")
    assert output == []
    assert not any(call[0] == "drive" for call in motor.calls)


def test_quit_ends_the_session_braked():
    motor = RecordingMotor()
    run(motor, "q", "-6 0.5")
    assert motor.calls == [("stop",)]


def test_end_of_input_ends_the_session_braked():
    motor = RecordingMotor()
    run(motor, "-3 0.25")
    assert motor.calls[-1] == ("stop",)


def test_ctrl_c_brakes_before_propagating():
    motor = RecordingMotor()
    with pytest.raises(KeyboardInterrupt):
        run(motor, "-3 0.25", then=KeyboardInterrupt)
    assert motor.calls[-1] == ("stop",)


def test_ctrl_c_during_a_move_brakes():
    motor = RecordingMotor()

    def sleep(seconds):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_calibration(motor, "mouth", MOUTH, SUPPLY, STEP_S, scripted_input("-6 2"), lambda text: None, sleep)
    assert motor.calls[0] == ("drive", -2048)
    assert motor.calls[-1] == ("stop",)


def test_failed_drive_still_brakes_and_propagates():
    motor = MotorThatFailsToDrive()
    with pytest.raises(OSError):
        run(motor, "-6 0.5")
    assert motor.calls[-1] == ("stop",)


def test_named_pose_drives_then_brakes():
    motor = RecordingMotor()
    output, slept = run(motor, "close")
    assert motor.calls[:7] == [("drive", 341)] * 6 + [("stop",)]
    assert slept == [STEP_S] * 6
    assert output == ["moved mouth +0.0 -> +1.0 V over 0.05 s, +1.0 V for 0.25 s, braked"]
