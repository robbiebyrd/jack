import pytest

from jack.show.control.commands import SetMouthMode, SetValue, StartPose, apply
from jack.show.control.control_board import ControlBoard
from jack.show.control.osc_feedback import state_messages
from jack.show.control.osc_protocol import osc_command
from jack.show.control.osc_query import ATTRIBUTES, READ, WRITE, answer, host_info, namespace
from tests.fakes import FakeClock
from tests.profiles import PROFILES

OSC_PORT = 9000


def board(mode="show"):
    return ControlBoard(PROFILES, 0.5, mode, FakeClock())


def methods(node):
    """Every node with an ACCESS other than 0, depth first."""
    found = [node] if node["ACCESS"] else []
    for child in node.get("CONTENTS", {}).values():
        found += methods(child)
    return found


def example_args(node):
    """Arguments a client reading this node would send."""
    if "TYPE" not in node:
        return [1.0]
    value_range = node["RANGE"][0]
    if "VALS" in value_range:
        return [value_range["VALS"][-1]]
    return [{"f": float, "i": int}[node["TYPE"]](value_range["MAX"])]


def query(path, attribute=None, status=None):
    return answer(path, attribute, PROFILES, status or board().status(True), OSC_PORT)


def test_the_root_is_a_container_holding_jack():
    root = namespace(PROFILES, board().status(True))
    assert root["FULL_PATH"] == "/" and root["ACCESS"] == 0
    jack = root["CONTENTS"]["jack"]
    assert jack["FULL_PATH"] == "/jack"
    assert set(jack["CONTENTS"]) == {
        "mouth", "hand", "pivot", "elbow", "rest", "mumble", "ping", "status", "subscribe", "unsubscribe",
    }


def test_every_full_path_matches_where_the_node_sits():
    def check(node, path):
        assert node["FULL_PATH"] == (path or "/")
        for name, child in node.get("CONTENTS", {}).items():
            check(child, f"{path}/{name}")

    check(namespace(PROFILES, board().status(True)), "")


def test_the_readable_methods_are_exactly_the_feedback_messages_with_their_values():
    status = board().status(True)
    nodes = methods(namespace(PROFILES, status))
    readable = {node["FULL_PATH"]: node["VALUE"] for node in nodes if node["ACCESS"] & READ}
    assert readable == dict(state_messages(status))


def test_every_writable_method_takes_the_message_its_description_says():
    for node in methods(namespace(PROFILES, board().status(True))):
        if node["ACCESS"] & WRITE:
            assert osc_command(node["FULL_PATH"], example_args(node), PROFILES) is not None, node["FULL_PATH"]
            for overload in node.get("OVERLOADS", []):
                args = [overload["RANGE"][0]["VALS"][0], 1.0]
                assert osc_command(node["FULL_PATH"], args, PROFILES) is not None


def test_no_method_is_listed_that_jack_does_not_accept_or_report():
    for node in methods(namespace(PROFILES, board().status(True))):
        assert node["ACCESS"] in (1, 2, 3)
        assert node["DESCRIPTION"]


def test_a_motor_describes_its_range_poses_and_live_value():
    jack_board = board()
    apply(SetValue("hand", -0.25, -0.25), jack_board)
    hand = query("/jack/hand", status=jack_board.status(True))[1]
    assert hand["TYPE"] == "f" and hand["ACCESS"] == 3 and hand["VALUE"] == [-0.25]
    assert hand["RANGE"] == [{"MIN": -1.0, "MAX": 1.0}] and hand["CLIPMODE"] == ["both"]
    assert query("/jack/elbow", "RANGE")[1] == {"RANGE": [{"MIN": 0.0, "MAX": 1.0}]}
    pose = hand["CONTENTS"]["pose"]
    assert pose["TYPE"] == "s" and pose["RANGE"] == [{"VALS": ["curl"]}] and pose["ACCESS"] == 2
    assert set(pose["CONTENTS"]) == {"curl"}
    assert "TYPE" not in pose["CONTENTS"]["curl"] and "TYPE" not in hand["CONTENTS"]["rest"]


def test_the_mouth_mode_reads_back():
    jack_board = board("live")
    apply(SetMouthMode("show"), jack_board)
    assert query("/jack/mouth/mode", "VALUE", jack_board.status(True)) == (200, {"VALUE": ["show"]})
    assert query("/jack/mouth/mode/show", "VALUE", jack_board.status(True)) == (200, {"VALUE": [1.0]})
    assert query("/jack/mouth/mode", "RANGE")[1] == {"RANGE": [{"VALS": ["live", "show"]}]}


def test_a_pose_playing_reads_zero_like_the_fader_feedback():
    jack_board = board()
    apply(StartPose("elbow", "up", None), jack_board)
    assert query("/jack/elbow", "VALUE", jack_board.status(True)) == (200, {"VALUE": [0.0]})


def test_host_info_names_the_udp_osc_port_and_extensions_whatever_the_path():
    expected = (200, host_info(OSC_PORT))
    assert query("/", "HOST_INFO") == expected
    assert query("/no/such/path", "HOST_INFO") == expected
    info = expected[1]
    assert info["OSC_PORT"] == 9000 and info["OSC_TRANSPORT"] == "UDP"
    assert info["EXTENSIONS"]["VALUE"] is True and info["EXTENSIONS"]["LISTEN"] is False


def test_every_supported_extension_can_be_queried():
    assert {key for key, on in host_info(OSC_PORT)["EXTENSIONS"].items() if on} < ATTRIBUTES


@pytest.mark.parametrize("path", ["/nope", "/jack/nope", "/jack/hand/pose/wave", "/jack/hand/volts/x"])
def test_an_unknown_path_is_a_404(path):
    assert query(path) == (404, None)


@pytest.mark.parametrize("attribute", ["NOPE", "LISTEN", "UNIT", "HTML", "value"])
def test_an_unknown_or_unsupported_attribute_is_a_400(attribute):
    assert query("/jack/hand", attribute) == (400, None)


def test_the_value_of_a_write_only_method_or_a_container_is_a_204():
    assert query("/jack/rest", "VALUE") == (204, None)
    assert query("/jack", "VALUE") == (204, None)


def test_an_attribute_the_node_lacks_is_an_empty_object():
    assert query("/jack", "TYPE") == (200, {})
    assert query("/jack/rest", "RANGE") == (200, {})


def test_a_trailing_slash_names_the_same_node():
    assert query("/jack/hand/") == query("/jack/hand")
    assert query("/jack/mumble", "FULL_PATH") == (200, {"FULL_PATH": "/jack/mumble"})
