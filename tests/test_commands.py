import pytest

from jack.show.control.commands import CommandError, RestCommand, SetMouthMode, SetValue, StartPose, apply
from jack.show.control.control_board import ControlBoard
from tests.fakes import FakeClock
from tests.profiles import PROFILES


def board(mode="live"):
    return ControlBoard(PROFILES, 0.5, mode, FakeClock())


def test_a_command_error_is_a_400_unless_it_says_otherwise():
    assert CommandError("bad").status == 400
    assert CommandError("gone", 404).status == 404
    assert isinstance(CommandError("bad"), ValueError)


def test_apply_drives_the_board():
    b = board()
    apply(SetValue("hand", 1.0, 1.0), b)
    assert b.target_volts("hand") == 2.0
    apply(StartPose("elbow", "up", None), b)
    assert b.target_volts("elbow") == 2.0
    apply(RestCommand(None), b)
    assert b.target_volts("hand") is None
    apply(SetMouthMode("show"), b)
    assert b.mouth_mode == "show"


@pytest.mark.parametrize("command", [SetValue("mouth", 0.5, 0.5), StartPose("mouth", "open", None)])
def test_mouth_commands_conflict_with_live_mode(command):
    b = board("live")
    with pytest.raises(CommandError) as error:
        apply(command, b)
    assert error.value.status == 409
    assert b.target_volts("mouth") is None


def test_mouth_commands_work_in_show_mode():
    b = board("show")
    apply(SetValue("mouth", 1.0, 1.0), b)
    assert b.target_volts("mouth") == -6.0
