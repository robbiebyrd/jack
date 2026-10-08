import math

import pytest

from jack.show.control.commands import CommandError, RestCommand, SetMouthMode, SetValue, StartPose
from jack.show.control.routes import argument_names, command_for, route_parts
from tests.profiles import PROFILES


def http(path, body=None):
    """What an HTTP request means: the path is the route, the JSON body its named parameters."""
    return command_for(path, body or {}, PROFILES)


def test_routes():
    assert http("/hand", {"value": 0.5}) == SetValue("hand", 0.5, 0.5)
    assert http("/hand/pose", {"name": "curl"}) == StartPose("hand", "curl", None)
    assert http("/hand/pose", {"name": "curl", "seconds": 2}) == StartPose("hand", "curl", 2.0)
    assert http("/hand/rest") == RestCommand("hand")
    assert http("/rest") == RestCommand(None)
    assert http("/mouth/mode", {"mode": "live"}) == SetMouthMode("live")


def test_values_are_clamped_to_the_motors_range():
    assert http("/hand", {"value": 1.4}) == SetValue("hand", 1.0, 1.4)
    assert http("/elbow", {"value": -0.2}) == SetValue("elbow", 0.0, -0.2)
    assert http("/hand", {"value": -1.5}) == SetValue("hand", -1.0, -1.5)
    assert http("/pivot", {"value": -1.5}) == SetValue("pivot", -1.0, -1.5)


@pytest.mark.parametrize(
    "path, body, status",
    [
        ("/tail", {"value": 0.5}, 404),
        ("/hand/wave", {}, 404),
        ("/hand/pose", {"name": "wave"}, 404),
        ("/elbow/pose/up", {}, 404),  # the per-pose address is OSC only
        ("/hand", {}, 400),
        ("/hand", {"value": "half"}, 400),
        ("/hand", {"value": True}, 400),
        ("/hand", {"value": math.nan}, 400),
        ("/hand", {"value": 10**400}, 400),  # too large for a float
        ("/hand", {"value": 0.5, "speed": 2}, 400),
        ("/hand/pose", {}, 400),
        ("/hand/pose", {"name": "curl", "seconds": 0}, 400),
        ("/mouth/mode", {"mode": "auto"}, 400),
        ("/rest", {"motor": "hand"}, 400),
    ],
)
def test_bad_routes_are_rejected_with_a_status(path, body, status):
    with pytest.raises(CommandError) as error:
        command_for(path, body, PROFILES)
    assert error.value.status == status


def test_unexpected_fields_are_named():
    with pytest.raises(CommandError, match="speed"):
        http("/hand", {"value": 0.5, "speed": 2})


@pytest.mark.parametrize("path", ["/ping", "/status", "/subscribe", "/unsubscribe"])
def test_osc_only_routes_are_not_commands(path):
    with pytest.raises(CommandError) as error:
        command_for(path, {}, PROFILES)
    assert error.value.status == 404


def test_route_parts_skip_empty_segments():
    assert route_parts("/jack//hand/") == ["jack", "hand"]


def test_osc_argument_names_match_the_http_fields():
    assert argument_names(["hand"]) == ["value"]
    assert argument_names(["hand", "pose"]) == ["name", "seconds"]
    assert argument_names(["mouth", "mode"]) == ["mode"]
    assert argument_names(["rest"]) == []
    assert argument_names(["hand", "rest"]) == []
