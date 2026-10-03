from pathlib import Path

import py2tosc
from py2tosc.enums import Conversion, PartialType, TriggerCondition

from tools.touchosc_layout import build_layout
from tests.profiles import PROFILES

PORT = 21601


def address(message) -> str:
    return "".join(partial.value for partial in message.path)


def bindings(doc):
    """(address, message, control) for every OSC binding in the layout."""
    return [
        (address(message), message, control)
        for control in doc.walk()
        for message in control.messages
        if isinstance(message, py2tosc.OscMessage)
    ]


def sent(doc):
    return {addr for addr, message, _ in bindings(doc) if message.send}


def received(doc):
    return {addr for addr, message, _ in bindings(doc) if message.receive}


def test_the_layout_is_valid_with_no_warnings():
    assert build_layout(PROFILES, PORT).validate() == []


def test_every_command_has_a_control():
    doc = build_layout(PROFILES, PORT)
    expected = {"/jack/subscribe", "/jack/ping", "/jack/rest", "/jack/mouth/mode/show"}
    for motor, profile in PROFILES.items():
        expected |= {f"/jack/{motor}", f"/jack/{motor}/rest"}
        expected |= {f"/jack/{motor}/pose/{pose}" for pose in profile.poses}
    assert expected <= sent(doc)


def test_every_feedback_message_has_a_control():
    doc = build_layout(PROFILES, PORT)
    expected = {"/jack/mumble", "/jack/mouth/mode/show"}
    for motor in PROFILES:
        expected |= {f"/jack/{motor}", f"/jack/{motor}/volts", f"/jack/{motor}/max_hold"}
    assert expected <= received(doc)


def test_subscribe_sends_the_receive_port_as_an_integer_on_press_only():
    doc = build_layout(PROFILES, PORT)
    [(_, message, _)] = [b for b in bindings(doc) if b[0] == "/jack/subscribe"]
    [argument] = message.arguments
    assert (argument.type, argument.conversion, argument.value) == (PartialType.CONSTANT, Conversion.INTEGER, str(PORT))
    assert [trigger.condition for trigger in message.triggers] == [TriggerCondition.RISE]


def test_buttons_fire_on_press_only():
    doc = build_layout(PROFILES, PORT)
    button_addresses = [a for a, m, c in bindings(doc) if m.send and c.control_type.value == "BUTTON"
                        and a != "/jack/mouth/mode/show"]
    assert button_addresses
    for addr, message, control in bindings(doc):
        if addr in button_addresses and message.send:
            assert [t.condition for t in message.triggers] == [TriggerCondition.RISE], addr


def test_the_pivot_fader_is_centred_and_spans_minus_one_to_one():
    doc = build_layout(PROFILES, PORT)
    [(_, message, control)] = [b for b in bindings(doc) if b[0] == "/jack/pivot"]
    [argument] = message.arguments
    assert (argument.scale_min, argument.scale_max) == (-1.0, 1.0)
    assert control.get("centered") is True


def test_the_mouth_mode_control_is_a_toggle():
    doc = build_layout(PROFILES, PORT)
    [(_, _, control)] = [b for b in bindings(doc) if b[0] == "/jack/mouth/mode/show"]
    assert control.get("buttonType") == py2tosc.enums.ButtonType.TOGGLE_PRESS


def test_the_saved_file_loads_back(tmp_path):
    path = tmp_path / "jack.tosc"
    build_layout(PROFILES, PORT).save(path)
    assert sent(py2tosc.load(path)) == sent(build_layout(PROFILES, PORT))


def test_the_repo_layout_file_is_up_to_date():
    from main import POSES_PATHS
    from jack.show.motion.poses import load_profiles

    repo_file = Path(__file__).resolve().parent.parent / "touchosc" / "jack.tosc"
    profiles = load_profiles([POSES_PATHS[0]], 12.0)
    assert sent(py2tosc.load(repo_file)) == sent(build_layout(profiles, PORT))
