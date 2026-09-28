import pytest

from motor_test.calibration import MAX_MOVE_S, parse_command, run_calibration
from motor_test.mouth import CLOSE, RELAX, hold, open_fully
from tests.fakes import RecordingMotor, drives

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


def run(motor, *lines, then=EOFError):
    output, slept = [], []
    run_calibration(motor, SUPPLY, STEP_S, scripted_input(*lines, then=then), output.append, slept.append)
    return output, slept


def test_parses_volts_and_seconds_as_a_hold():
    assert parse_command("-4.5 0.8", SUPPLY) == (hold(-4.5, 0.8),)


def test_close_relax_and_open_name_the_calibrated_poses():
    assert parse_command("close", SUPPLY) == CLOSE
    assert parse_command("relax", SUPPLY) == RELAX
    assert parse_command("open 0.5", SUPPLY) == open_fully(0.5)


@pytest.mark.parametrize("line", ["q", "quit", "  q  "])
def test_q_means_quit(line):
    assert parse_command(line, SUPPLY) is None


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
        "open",  # open needs a duration
        f"open {MAX_MOVE_S + 0.1}",  # open longer than the stall-safety cap
        "close 1",  # close takes no argument
    ],
)
def test_bad_commands_are_rejected(line):
    with pytest.raises(ValueError):
        parse_command(line, SUPPLY)


def test_move_drives_step_by_step_for_the_duration_then_brakes():
    motor = RecordingMotor()
    output, slept = run(motor, "-6 0.5")
    assert motor.calls[:11] == [("drive", -2048)] * 10 + [("stop",)]
    assert slept == [STEP_S] * 10
    assert output == ["moved B -6.0 V for 0.5 s, braked"]


def test_duration_that_is_not_whole_steps_is_rejected_without_moving():
    motor = RecordingMotor()
    output, slept = run(motor, "-4.5 0.33")
    assert drives(motor) == []
    assert slept == []
    assert len(output) == 1 and "steps" in output[0]


def test_open_ramps_up_before_holding():
    motor = RecordingMotor()
    run(motor, "open 0.5")
    assert drives(motor) == [-410, -819, -1229, -1638, -2048] + [-2048] * 10


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
        run_calibration(motor, SUPPLY, STEP_S, scripted_input("-6 2"), lambda text: None, sleep)
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
    assert motor.calls[:6] == [("drive", 341)] * 5 + [("stop",)]
    assert slept == [STEP_S] * 5
    assert output == ["moved B +1.0 V for 0.25 s, braked"]
