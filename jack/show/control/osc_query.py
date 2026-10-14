"""OSCQuery: Jack's OSC address space described as JSON, so a client can browse it instead of being told it.

Follows https://github.com/Vidvox/OSCQueryProposal: the core attributes (FULL_PATH, CONTENTS, TYPE,
HOST_INFO) and the optional ACCESS, VALUE, RANGE, DESCRIPTION, CLIPMODE and OVERLOADS. VALUE is read
from the same state messages the OSC feedback sends, so the two can't disagree. No websocket, so no
LISTEN streaming (use /jack/subscribe). See "OSCQuery" in SPEC.md.
"""

from collections.abc import Mapping

from jack.show.control.control_board import MOUTH_MODES
from jack.show.control.osc_feedback import state_messages
from jack.show.control.osc_protocol import OSC_PREFIX
from jack.show.control.status import Status
from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.motion.poses import MotorProfile

# ACCESS is a bit mask.
NO_VALUE, READ, WRITE = 0, 1, 2
READ_WRITE = READ | WRITE

EXTENSIONS = {
    "ACCESS": True,
    "VALUE": True,
    "RANGE": True,
    "DESCRIPTION": True,
    "CLIPMODE": True,
    "OVERLOADS": True,
    "TAGS": False,
    "EXTENDED_TYPE": False,
    "UNIT": False,
    "CRITICAL": False,
    "HTML": False,
    "LISTEN": False,
    "PATH_CHANGED": False,
}
# Attributes a client may ask for one at a time (`/jack/hand?VALUE`).
ATTRIBUTES = frozenset({"FULL_PATH", "CONTENTS", "TYPE"} | {key for key, supported in EXTENSIONS.items() if supported})

Node = dict[str, object]


def host_info(osc_port: int) -> Node:
    """The HOST_INFO answer: where the OSC server is and which optional attributes this server provides."""
    return {"NAME": "Jack", "OSC_PORT": osc_port, "OSC_TRANSPORT": "UDP", "EXTENSIONS": dict(EXTENSIONS)}


def namespace(profiles: Mapping[str, MotorProfile], status: Status) -> Node:
    """The whole address space from `/`, with each readable method's VALUE taken from `status`."""
    values = dict(state_messages(status))

    def method(path: str, description: str, access: int, **attributes: object) -> Node:
        node: Node = {"FULL_PATH": path, "DESCRIPTION": description, "ACCESS": access, **attributes}
        if access & READ:
            node["VALUE"] = values[path]
        return node

    def button(path: str, description: str, **attributes: object) -> Node:
        # No TYPE: a button sends no argument, or 1 to press (a release of 0 is ignored).
        return method(path, f"{description} (no argument, or a non-zero number)", WRITE, **attributes)

    jack = OSC_PREFIX
    contents: dict[str, Node] = {}
    for name in MOTOR_NAMES:
        base = f"{jack}/{name}"
        low = -1.0 if motor_spec(name).two_sided else 0.0
        poses = sorted(profiles[name].poses)
        motor = method(
            base, f"{name}: continuous value, clamped to {low:g}..1; reads 0 at rest or during a pose", READ_WRITE,
            TYPE="f", RANGE=[{"MIN": low, "MAX": 1.0}], CLIPMODE=["both"],
        )
        motor["CONTENTS"] = {
            "volts": method(f"{base}/volts", f"volts the {name} motor was last driven at", READ, TYPE="f"),
            "max_hold": method(
                f"{base}/max_hold", "1 while the motor's max hold has tripped", READ,
                TYPE="i", RANGE=[{"MIN": 0, "MAX": 1}],
            ),
            "pose": method(
                f"{base}/pose", "play a named pose, optionally over that many seconds", WRITE,
                TYPE="s", RANGE=[{"VALS": poses}],
                OVERLOADS=[{"TYPE": "sf", "RANGE": [{"VALS": poses}, {"MIN": 0.0}]}],
                CONTENTS={pose: button(f"{base}/pose/{pose}", f"play the {pose} pose") for pose in poses},
            ),
            "rest": button(f"{base}/rest", f"rest the {name} motor"),
        }
        contents[name] = motor
    mouth = contents["mouth"]["CONTENTS"]
    mouth["mode"] = method(
        f"{jack}/mouth/mode", "the mouth's source: live follows the voice, show follows commands", READ_WRITE,
        TYPE="s", RANGE=[{"VALS": list(MOUTH_MODES)}],
        CONTENTS={"show": method(
            f"{jack}/mouth/mode/show", "1 for show mode, 0 for live (for a toggle button)", READ_WRITE,
            TYPE="f", RANGE=[{"MIN": 0.0, "MAX": 1.0}],
        )},
    )
    port_range = [{"MIN": 1024, "MAX": 65535}]
    contents |= {
        "rest": button(f"{jack}/rest", "rest every motor"),
        "mumble": method(
            f"{jack}/mumble", "1 while Mumble is connected", READ, TYPE="i", RANGE=[{"MIN": 0, "MAX": 1}],
        ),
        "ping": button(f"{jack}/ping", f"reply {jack}/pong to the sender"),
        "status": button(f"{jack}/status", "reply with every state message once"),
        "subscribe": method(
            f"{jack}/subscribe", "push state feedback to the sender for 60 s, at this port or the reply port",
            WRITE, TYPE="i", RANGE=port_range,
        ),
        "unsubscribe": method(
            f"{jack}/unsubscribe", "stop pushing feedback to the sender at this port or the reply port",
            WRITE, TYPE="i", RANGE=port_range,
        ),
    }
    return {
        "FULL_PATH": "/", "DESCRIPTION": "Jack", "ACCESS": NO_VALUE,
        "CONTENTS": {"jack": {
            "FULL_PATH": jack, "DESCRIPTION": "Jack's motors, mouth mode and feedback", "ACCESS": NO_VALUE,
            "CONTENTS": contents,
        }},
    }


def answer(
    path: str, attribute: str | None, profiles: Mapping[str, MotorProfile], status: Status, osc_port: int,
) -> tuple[int, Node | None]:
    """The HTTP status and JSON for an OSCQuery request: a node, one of its attributes, or HOST_INFO.

    204 asks for the VALUE of a method that has none to read; 400 an attribute this server doesn't know;
    404 a path that isn't in the address space. A known attribute the node lacks is an empty object.
    """
    if attribute == "HOST_INFO":
        return 200, host_info(osc_port)
    if attribute is not None and attribute not in ATTRIBUTES:
        return 400, None
    node = _find(namespace(profiles, status), path)
    if node is None:
        return 404, None
    if attribute is None:
        return 200, node
    if attribute == "VALUE" and not int(node.get("ACCESS", READ_WRITE)) & READ:
        return 204, None
    return 200, {attribute: node[attribute]} if attribute in node else {}


def _find(root: Node, path: str) -> Node | None:
    node = root
    for part in (part for part in path.split("/") if part):
        contents = node.get("CONTENTS")
        if not isinstance(contents, dict) or part not in contents:
            return None
        node = contents[part]
    return node
