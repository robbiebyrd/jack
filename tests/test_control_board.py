import threading

import pytest

from jack.show.control.control_board import ControlBoard
from jack.show.control.status import PoseCommand, ValueCommand
from tests.fakes import FakeClock
from tests.profiles import PROFILES


def board(mode="live", timeout_s=0.5):
    clock = FakeClock()
    return ControlBoard(PROFILES, timeout_s, mode, clock), clock


def test_no_command_means_rest():
    b, _ = board()
    assert b.target_volts("hand") is None


def test_value_maps_through_the_motor_profile():
    b, _ = board()
    b.set_value("hand", 1.0)
    assert b.target_volts("hand") == 2.0
    b.set_value("pivot", -1.0)
    assert b.target_volts("pivot") == -2.0


def test_value_holds_until_the_timeout_then_rests():
    b, clock = board()
    b.set_value("hand", 1.0)
    clock.advance(0.5)
    assert b.target_volts("hand") == 2.0
    clock.advance(0.01)
    assert b.target_volts("hand") is None


def test_each_new_value_restarts_the_timeout():
    b, clock = board()
    b.set_value("hand", 1.0)
    clock.advance(0.4)
    b.set_value("hand", 0.5)
    clock.advance(0.4)
    assert b.target_volts("hand") == 1.5


def test_pose_plays_for_its_default_duration():
    b, clock = board()
    b.start_pose("elbow", "up")
    clock.advance(0.4)
    assert b.target_volts("elbow") == 2.0
    clock.advance(0.1)  # 0.4 + 0.1 is exactly 0.5 in binary floating point
    assert b.target_volts("elbow") is None


def test_pose_duration_can_be_given():
    b, clock = board()
    b.start_pose("elbow", "up", 2.0)
    clock.advance(1.9)
    assert b.target_volts("elbow") == 2.0


def test_unknown_pose_is_rejected():
    b, _ = board()
    with pytest.raises(ValueError, match="wave"):
        b.start_pose("hand", "wave")


def test_new_command_replaces_the_old_one():
    b, _ = board()
    b.start_pose("pivot", "left", 5.0)
    b.set_value("pivot", 1.0)
    assert b.target_volts("pivot") == 2.0


def test_rest_and_rest_all():
    b, _ = board()
    b.set_value("hand", 1.0)
    b.set_value("elbow", 1.0)
    b.rest("hand")
    assert b.target_volts("hand") is None and b.target_volts("elbow") == 2.0
    b.rest_all()
    assert b.target_volts("elbow") is None


def test_mouth_mode_switches_and_is_validated():
    b, _ = board()
    assert b.mouth_mode == "live"
    b.set_mouth_mode("show")
    assert b.mouth_mode == "show"
    with pytest.raises(ValueError):
        b.set_mouth_mode("auto")
    with pytest.raises(ValueError):
        ControlBoard(PROFILES, 0.5, "auto", FakeClock())
    with pytest.raises(ValueError):
        ControlBoard(PROFILES, 0.0, "live", FakeClock())


def test_changing_the_mouth_mode_drops_the_mouths_command_only():
    b, _ = board("show")
    b.set_value("mouth", 1.0)
    b.set_value("hand", 1.0)
    b.set_mouth_mode("live")
    assert b.target_volts("mouth") is None
    assert b.target_volts("hand") == 2.0


def test_setting_the_same_mouth_mode_keeps_the_mouths_command():
    b, _ = board("show")
    b.set_value("mouth", 1.0)
    b.set_mouth_mode("show")
    assert b.target_volts("mouth") == -6.0


def test_status_reports_mode_commands_reports_and_motor_facts():
    b, clock = board()
    b.set_value("hand", 0.5)
    b.start_pose("elbow", "up")
    clock.advance(0.2)
    b.report("hand", 1.5, False)
    status = b.status(mumble_connected=True)
    assert status.mouth_mode == "live" and status.mumble_connected is True
    assert status.motors["hand"].command == ValueCommand(value=0.5, age_s=0.2)
    assert status.motors["elbow"].command == PoseCommand(pose="up", seconds_left=0.3)
    assert status.motors["hand"].volts == 1.5
    assert status.motors["hand"].max_hold_tripped is False
    assert status.motors["pivot"].two_sided is True
    assert status.motors["pivot"].poses == ("left", "right")
    assert status.motors["mouth"].calibrated is True
    assert status.motors["mouth"].command is None


def test_status_serialises_to_the_json_document_the_control_page_reads():
    b, clock = board()
    b.set_value("hand", 0.5)
    clock.advance(0.2)
    document = b.status(mumble_connected=False).to_json()
    assert document["mouth_mode"] == "live" and document["mumble_connected"] is False
    assert document["motors"]["hand"]["command"] == {"value": 0.5, "age_s": 0.2}
    assert document["motors"]["mouth"]["command"] is None
    assert document["motors"]["pivot"]["poses"] == ("left", "right")  # json.dumps writes a tuple as a list
    fields = {"command", "volts", "max_hold_tripped", "calibrated", "two_sided", "poses"}
    assert set(document["motors"]["hand"]) == fields


def test_concurrent_writers_and_reader_see_whole_commands():
    b, _ = board()
    stop = threading.Event()
    seen = set()

    def write(value):
        while not stop.is_set():
            b.set_value("hand", value)

    def read():
        for _ in range(5000):
            seen.add(b.target_volts("hand"))

    writers = [threading.Thread(target=write, args=(v,)) for v in (0.0, 1.0)]
    for thread in writers:
        thread.start()
    read()
    stop.set()
    for thread in writers:
        thread.join()
    assert seen <= {None, 0.0, 2.0}
