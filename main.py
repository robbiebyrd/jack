"""Jack talks and takes show control: Boss's voice from Mumble plays on the 3.5 mm jack; the mouth follows it (live) or show commands (show); hand, pivot and elbow follow OSC/HTTP show commands."""

import dataclasses
import math
import os
import random
import signal
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from jack.adapters.audio.alsa_sink import open_alsa_sink
from jack.support.attempt_all import attempt_all
from jack.show.control.control_board import MOUTH_MODES, ControlBoard
from jack.adapters.network.http_server import start_http_server
from jack.show.motion.motors import MOTORS
from jack.show.motion.mouth import CLOSE, RELAX, STEP_S, Segment, open_fully, rest
from jack.adapters.audio.mumble_voice import MumbleVoice, connect_mumble
from jack.show.control.osc_feedback import Subscribers
from jack.adapters.network.osc_server import OscEndpoint, start_feedback
from jack.adapters.hardware.pca9685 import Pca9685
from jack.show.motion.poses import MotorProfile, load_profiles
from jack.show.motion.ramp import constant_profile, segment_profile
from jack.support.rate_limited_log import RateLimitedLog
from jack.show.control.show_commands import OscContext, handle_osc
from jack.show.motion.speech import random_phrase
from jack.adapters.system.systemd_notify import notify
from jack.application.talk_loop import run_talk_loop
from jack.show.audio.talk_settings import TalkSettings
from jack.adapters.hardware.tb6612_motor import MOTOR_CHANNELS, Tb6612Motor

I2C_BUS = 1
PWM_FREQ_HZ = 50

SUPPLY_VOLTS = 12.0
# The 3.5 mm jack; plughw converts formats if the card needs it (Task 1 of the talk plan checked it).
ALSA_DEVICE = "plughw:CARD=Headphones,DEV=0"
ALSA_PERIODS = 4
# mumble-server runs on the Pi itself; Boss's client connects to 10.10.0.54:64738.
MUMBLE_HOST = "127.0.0.1"
MUMBLE_PORT = 64738
MUMBLE_USER = "Jack"
MUMBLE_PASSWORD_ENV = "JACK_MUMBLE_PASSWORD"
# Any TalkSettings field can be overridden in /etc/jack/jack.env as JACK_<FIELD>.
SETTING_ENV_PREFIX = "JACK_"
# Repo defaults, then the Pi's override file (SPEC.md "Poses and per-motor settings").
POSES_PATHS = (Path(__file__).resolve().parent / "poses.toml", Path("/etc/jack/poses.toml"))
# Mouth settings that moved from jack.env to the mouth's entry in poses.toml: env field -> poses.toml key.
MOVED_TO_POSES = {
    "open_min_v": "min_v",
    "open_max_v": "max_v",
    "open_slew_v_per_s": "slew_v_per_s",
    "close_v": "rest_pulse_v",
    "close_s": "rest_pulse_s",
}
# Show control (SPEC.md "Show control"); every value can be set in /etc/jack/jack.env.
DEFAULT_OSC_PORT = 9000
DEFAULT_HTTP_PORT = 8080
DEFAULT_CONTROL_TIMEOUT_S = 0.5
# One log line per kind of bad OSC message per minute.
OSC_LOG_INTERVAL_S = 60.0
# Fixed pose tour: an alternative to speaking, e.g. for checking the mechanism.
MOUTH_DEMO = (*CLOSE, *rest(1.5), *RELAX, *rest(1.5), *open_fully(0.5))


def exit_on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so the playback loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the app is still running its loop."""
    notify("WATCHDOG=1")


def watchdog_while_connected(mumble_client) -> Callable[[], None]:
    """The talk loop's once-a-second callback: ping the watchdog only while the Mumble client thread lives.

    pymumble's thread ends for good when the server rejects the login, so a dead one must restart the app.
    """

    def check() -> None:
        if not mumble_client.is_alive():
            raise SystemExit(
                "Mumble client stopped (e.g. the server rejected the password); exiting so systemd restarts Jack"
            )
        ping_watchdog()

    return check


def speaking_cycle(rng: random.Random) -> list[list[int]]:
    """One random talking phrase on the mouth, with motor B off. Counts are [motor A, motor B]."""
    return _mouth_only(random_phrase(rng))


def demo_cycle() -> list[list[int]]:
    """MOUTH_DEMO on the mouth, with motor B off. Counts are [motor A, motor B]."""
    return _mouth_only(MOUTH_DEMO)


def _mouth_only(segments: tuple[Segment, ...]) -> list[list[int]]:
    mouth = segment_profile(segments, SUPPLY_VOLTS, STEP_S)
    return [mouth, constant_profile(0.0, SUPPLY_VOLTS, len(mouth))]


def mumble_password(environ: Mapping[str, str] = os.environ) -> str:
    """The Mumble server password, which systemd loads from /etc/jack/jack.env."""
    password = environ.get(MUMBLE_PASSWORD_ENV, "")
    if not password:
        raise SystemExit(
            f"{MUMBLE_PASSWORD_ENV} is empty or unset. Put the Mumble server password in "
            "/etc/jack/jack.env, then: sudo systemctl restart jack"
        )
    return password


def talk_settings(environ: Mapping[str, str] = os.environ) -> TalkSettings:
    """TalkSettings defaults, with any JACK_<FIELD> environment variable (e.g. JACK_GATE_OPEN_DB=-20) overriding that field."""
    for field, key in MOVED_TO_POSES.items():
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise SystemExit(
                f"{var} moved to the mouth's entry in /etc/jack/poses.toml as {key}; "
                "remove it from /etc/jack/jack.env, then: sudo systemctl restart jack"
            )
    overrides: dict[str, float] = {}
    for field in dataclasses.fields(TalkSettings):
        var = SETTING_ENV_PREFIX + field.name.upper()
        number = _env_number(environ, var, None)
        if number is None:
            continue
        overrides[field.name] = number
    try:
        settings = TalkSettings(**overrides)
    except ValueError as error:
        raise SystemExit(f"Mouth settings from /etc/jack/jack.env are invalid: {error}") from error
    return settings


def motor_profiles(paths: tuple[Path, ...] = POSES_PATHS) -> dict[str, MotorProfile]:
    """Every motor's profile from poses.toml, or a one-line exit naming the problem."""
    try:
        return load_profiles(paths, SUPPLY_VOLTS)
    except (ValueError, OSError) as error:
        raise SystemExit(f"Motor settings are invalid: {error}") from error


def describe_overrides(settings: TalkSettings) -> str:
    """The fields that differ from the defaults, as 'name=value, ...'; empty when there are none."""
    defaults = TalkSettings()
    return ", ".join(
        f"{field.name}={getattr(settings, field.name)}"
        for field in dataclasses.fields(TalkSettings)
        if getattr(settings, field.name) != getattr(defaults, field.name)
    )


@dataclass(frozen=True)
class ShowControlConfig:
    osc_port: int
    http_port: int
    timeout_s: float
    mouth_mode: str
    reply_port: int | None = None


def show_control_config(environ: Mapping[str, str] = os.environ) -> ShowControlConfig:
    """Ports, dead-man timeout and startup mouth mode, from jack.env or their defaults."""
    mode = environ.get("JACK_MOUTH_MODE") or "live"
    if mode not in MOUTH_MODES:
        raise SystemExit(f"JACK_MOUTH_MODE={mode!r} in /etc/jack/jack.env must be one of {', '.join(MOUTH_MODES)}")
    timeout = _env_number(environ, "JACK_CONTROL_TIMEOUT_S", DEFAULT_CONTROL_TIMEOUT_S)
    if timeout <= 0:
        raise SystemExit(f"JACK_CONTROL_TIMEOUT_S={timeout!r} in /etc/jack/jack.env must be positive")
    return ShowControlConfig(
        osc_port=_env_port(environ, "JACK_OSC_PORT", DEFAULT_OSC_PORT),
        http_port=_env_port(environ, "JACK_HTTP_PORT", DEFAULT_HTTP_PORT),
        timeout_s=timeout,
        mouth_mode=mode,
        reply_port=_env_port(environ, "JACK_OSC_REPLY_PORT", None),
    )


def _env_number(environ: Mapping[str, str], var: str, default: float | None) -> float | None:
    value = environ.get(var, "")
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise SystemExit(f"{var}={value!r} in /etc/jack/jack.env is not a number")
    return number


def _env_port(environ: Mapping[str, str], var: str, default: int | None) -> int | None:
    value = environ.get(var, "")
    if not value:
        return default
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= 65535:
        raise SystemExit(f"{var}={value!r} in /etc/jack/jack.env must be a port number 1-65535")
    return int(value)


def build_motors(bus) -> dict[str, Tb6612Motor]:
    """Every motor on its HAT and channel, braked as soon as its HAT is up.

    A crash without cleanup can leave a HAT driving at its last duty, so each HAT's motors are
    braked before the next HAT is tried. A HAT that doesn't answer stops the app naming its address.
    """
    motors: dict[str, Tb6612Motor] = {}
    for address in dict.fromkeys(spec.address for spec in MOTORS):
        try:
            chip = Pca9685(bus, address, PWM_FREQ_HZ)
            hat_motors = {
                spec.name: Tb6612Motor(chip, MOTOR_CHANNELS[spec.channel]) for spec in MOTORS if spec.address == address
            }
            attempt_all(motor.stop for motor in hat_motors.values())
        except OSError as error:
            raise SystemExit(
                f"Motor HAT at {address:#04x} is not responding ({error}); check it is seated and its address pads"
            ) from error
        motors.update(hat_motors)
    return motors


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    settings = talk_settings()
    profiles = motor_profiles()
    config = show_control_config()
    if overrides := describe_overrides(settings):
        print(f"Mouth setting overrides: {overrides}")
    board = ControlBoard(profiles, config.timeout_s, config.mouth_mode, time.monotonic)
    voice = MumbleVoice(settings.max_backlog_frames, print)
    mumble_client = connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, mumble_password())
    with SMBus(I2C_BUS) as bus:
        motors = build_motors(bus)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        endpoint = OscEndpoint("0.0.0.0", config.osc_port)
        subscribers = Subscribers(time.monotonic)
        osc_log = RateLimitedLog(print, OSC_LOG_INTERVAL_S, time.monotonic)
        context = OscContext(
            profiles=profiles, board=board, subscribers=subscribers, send=endpoint.send,
            mumble_connected=lambda: voice.connected, reply_port=config.reply_port, log=osc_log,
        )
        endpoint.serve(lambda address, args, sender: handle_osc(address, args, sender, context))
        start_feedback(endpoint, subscribers, lambda: board.status(voice.connected), osc_log)
        start_http_server("0.0.0.0", config.http_port, board, profiles, lambda: voice.connected)
        reply_target = config.reply_port or "sender's port"
        uncalibrated = [name for name, profile in profiles.items() if not profile.calibrated]
        print(
            f"Talking: Mumble voice on {ALSA_DEVICE}; motors {', '.join(motors)} on {SUPPLY_VOLTS} V; "
            f"mouth {config.mouth_mode}; OSC UDP {config.osc_port}, HTTP {config.http_port}; "
            f"OSC replies to {reply_target}"
            + (f"; uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
        )
        notify("READY=1")
        run_talk_loop(
            [voice], sink, motors, profiles, settings, board, SUPPLY_VOLTS, watchdog_while_connected(mumble_client)
        )


if __name__ == "__main__":
    main()
