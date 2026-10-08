"""Generate a TouchOSC layout (touchosc/jack.tosc) with a control for every Jack command and feedback message.

The motors and their poses come from poses.toml, so after calibrating new poses run this again:
    .venv/bin/python -m tools.touchosc_layout            # writes touchosc/jack.tosc
    .venv/bin/python -m tools.touchosc_layout --port 21601 --out touchosc/jack.tosc
Open the file in TouchOSC, set its OSC connection to host 10.10.0.54, send port 9000 and receive
port --port, then press Subscribe. See "TouchOSC support" in SPEC.md and the README.
"""

import argparse
from collections.abc import Mapping, Sequence
from pathlib import Path

import py2tosc
from py2tosc import Value, ui
from py2tosc.enums import ButtonType, Conversion

from jack.show.motion.motors import motor_spec
from jack.show.motion.poses import MotorProfile

DEFAULT_RECEIVE_PORT = 21601
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "touchosc" / "jack.tosc"
# A landscape tablet-sized canvas; TouchOSC scales it to the window.
FRAME = (0, 0, 1024, 768)

Colour = tuple[float, float, float, float]

GREY: Colour = (0.25, 0.25, 0.25, 1.0)
RED = (0.85, 0.2, 0.2, 1.0)
GREEN = (0.2, 0.75, 0.3, 1.0)
BLUE = (0.2, 0.45, 0.85, 1.0)
MOTOR_COLOURS = {"mouth": (0.85, 0.55, 0.2, 1.0), "hand": BLUE, "pivot": (0.6, 0.35, 0.8, 1.0), "elbow": GREEN}


def build_layout(profiles: Mapping[str, MotorProfile], receive_port: int) -> py2tosc.Document:
    """The whole dashboard: a header row of global controls over one column per motor."""
    header = ui.row(
        _press_button(
            "Subscribe", "/jack/subscribe", GREEN, args=[ui.const(str(receive_port), conversion=Conversion.INTEGER)]
        ),
        _press_button("Ping", "/jack/ping", GREY),
        _indicator("Mumble", "/jack/mumble", GREEN),
        _mouth_mode_toggle(),
        _press_button("REST ALL", "/jack/rest", RED),
        gap=8,
        name="header",
    )
    # Every column gets the same number of pose rows so faders and buttons line up across motors.
    pose_rows = max(len(profile.poses) for profile in profiles.values())
    motors = ui.row(
        *(_motor_column(name, profile, pose_rows) for name, profile in profiles.items()), gap=12, name="motors"
    )
    doc = py2tosc.Document(root=ui.column(header, motors, sizes=(1, 7), gap=12, pad=12, frame=FRAME, name="jack"))
    doc.resolve()
    return doc


def _motor_column(name: str, profile: MotorProfile, pose_rows: int) -> py2tosc.Control:
    colour = MOTOR_COLOURS.get(name, BLUE)
    low = -1.0 if motor_spec(name).two_sided else 0.0
    title = name if profile.calibrated else f"{name} (uncalibrated)"
    # TouchOSC doesn't resend a fader held still, so a released fader must send rest itself: it snaps
    # back to the value that means 0 to Jack (the middle of a two-sided fader).
    rest = 0.5 if low < 0 else 0.0
    fader = py2tosc.fader(
        name=f"{name}_fader",
        color=colour,
        centered=low < 0,
        values=[Value("x", default=rest, default_pull=100), Value("touch", default=False)],
        messages=[ui.osc(f"/jack/{name}", args=[ui.value("x", scale=(low, 1.0))])],
    )
    poses = [_press_button(pose, f"/jack/{name}/pose/{pose}", colour) for pose in profile.poses]
    spacers = [_spacer(f"{name}_spare_{n}") for n in range(pose_rows - len(poses))]
    return ui.column(
        _caption(title, name=f"{name}_title"),
        fader,
        _readout(f"{name}_volts", f"/jack/{name}/volts"),
        _indicator("MAX HOLD", f"/jack/{name}/max_hold", RED, name=f"{name}_max_hold"),
        *poses,
        *spacers,
        _press_button("Rest", f"/jack/{name}/rest", GREY, name=f"{name}_rest"),
        sizes=(1, 8, 1, 1, *([1] * pose_rows), 1),
        gap=6,
        name=name,
    )


def _spacer(name: str) -> py2tosc.Control:
    """An empty, invisible slot that keeps short columns aligned with the longest."""
    return py2tosc.box(name=name, background=False, outline=False)


def _press_button(
    caption: str, address: str, colour: Colour, *, args: Sequence[object] | None = None, name: str | None = None
) -> py2tosc.Control:
    """A momentary button that sends once, on press (its release would be ignored by Jack anyway)."""
    button = py2tosc.button(
        name=name or _slug(address),
        color=colour,
        messages=[ui.osc(address, args=args, on="RISE", receive=False)],
    )
    return ui.labelled(button, caption, size=16)


def _mouth_mode_toggle() -> py2tosc.Control:
    """A toggle that switches the mouth between live voice (off) and show control (on), and follows Jack's mode."""
    toggle = py2tosc.button(
        name="mouth_mode_show",
        color=BLUE,
        button_type=ButtonType.TOGGLE_PRESS,
        messages=[ui.osc("/jack/mouth/mode/show")],
    )
    return ui.labelled(toggle, "Mouth: show control", size=16)


def _indicator(caption: str, address: str, colour: Colour, *, name: str | None = None) -> py2tosc.Control:
    """A non-interactive light driven by a 0/1 feedback message."""
    light = py2tosc.button(
        name=name or _slug(address),
        color=colour,
        interactive=False,
        messages=[ui.osc(address, send=False)],
    )
    return ui.labelled(light, caption, size=14)


def _readout(name: str, address: str) -> py2tosc.Control:
    """A label showing a feedback value as text."""
    return py2tosc.label(
        name=name,
        text_size=16,
        values=[Value("text", default="0.0"), Value("touch", default=False)],
        messages=[ui.osc(address, args=[ui.value("text")], var="text", send=False)],
    )


def _caption(text: str, *, name: str) -> py2tosc.Control:
    return py2tosc.label(
        name=name,
        text_size=18,
        background=False,
        values=[Value("text", default=text), Value("touch", default=False)],
    )


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text).strip("_") or "control"


def main(argv: list[str] | None = None) -> int:
    from jack.show.motion.poses import load_profiles
    from main import POSES_PATHS, SUPPLY_VOLTS

    parser = argparse.ArgumentParser(description="Write a TouchOSC layout for Jack.")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_RECEIVE_PORT, help="the port TouchOSC receives on (1024-65535)"
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be 1024-65535 (Jack rejects lower subscribe ports)")
    # Repo defaults only: the layout describes the poses committed to the repo.
    profiles = load_profiles([POSES_PATHS[0]], SUPPLY_VOLTS)
    doc = build_layout(profiles, args.port)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(args.out)
    print(f"Wrote {args.out} (receive port {args.port})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
