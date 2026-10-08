"""Jack talks and takes show control.

Boss's voice from ROC or Mumble plays on the 3.5 mm jack; the mouth follows it (live) or show commands
(show); hand, pivot and elbow follow OSC/HTTP show commands.
"""

import dataclasses
import math
import os
import signal
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from jack.adapters.audio.alsa_sink import open_alsa_sink
from jack.adapters.audio.mumble_voice import MumbleVoice, connect_mumble
from jack.adapters.audio.roc_voice import RocVoice, roc_recv_command
from jack.adapters.hardware.pca9685 import I2CBus, Pca9685
from jack.adapters.hardware.tb6612_motor import MOTOR_CHANNELS, Tb6612Motor
from jack.adapters.network.http_server import start_http_server
from jack.adapters.network.osc_server import OscEndpoint, start_feedback
from jack.adapters.system.systemd_notify import notify
from jack.application.talk_loop import run_talk_loop
from jack.show.audio.lip_sync import check_mouth_gain
from jack.show.audio.talk_settings import TalkSettings
from jack.show.control.control_board import MOUTH_MODES, ControlBoard
from jack.show.control.osc_feedback import Subscribers
from jack.show.control.show_commands import OscContext, handle_osc
from jack.show.motion.motors import MOTORS
from jack.show.motion.poses import MotorProfile, load_profiles
from jack.support.attempt_all import attempt_all
from jack.support.rate_limited_log import RateLimitedLog

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
# Lip-sync settings replaced when the mouth began following the audio's amplitude (SPEC.md "Mouth control").
REPLACED_BY_MOUTH_GAIN = ("gate_open_db", "gate_close_db", "full_db", "open_curve")
# Show control (SPEC.md "Show control"); every value can be set in /etc/jack/jack.env.
DEFAULT_OSC_PORT = 9000
DEFAULT_HTTP_PORT = 8080
# Longer than the longest hold (the pivot's 4 s): TouchOSC doesn't resend a fader held still, and its
# faders send rest themselves on release, so this only covers a lost network or a crashed controller.
DEFAULT_CONTROL_TIMEOUT_S = 5.0
# Roc Toolkit voice (SPEC.md "ROC voice"); every value can be set in /etc/jack/jack.env.
DEFAULT_ROC_SOURCE_PORT = 10001
DEFAULT_ROC_REPAIR_PORT = 10002
DEFAULT_ROC_CONTROL_PORT = 10003
# The spike's lowest steady target over the Wi-Fi LAN was 90 ms; 100 ms leaves a margin.
DEFAULT_ROC_TARGET_LATENCY_MS = 100.0
# One log line per kind of bad OSC message per minute.
OSC_LOG_INTERVAL_S = 60.0


def exit_on_sigterm(_signum: int, _frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so the playback loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the app is still running its loop."""
    notify("WATCHDOG=1")


def watchdog_noting_mumble(mumble_client: threading.Thread, log: Callable[[str], None]) -> Callable[[], None]:
    """The talk loop's once-a-second callback: ping the watchdog, and log once if Mumble's client thread died.

    pymumble's thread ends for good when the server rejects the login. Jack keeps running on ROC; a
    restart retries Mumble.
    """
    noted = False

    def check() -> None:
        nonlocal noted
        if not noted and not mumble_client.is_alive():
            noted = True
            log("Mumble stopped; ROC still works")
        ping_watchdog()

    return check


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
    """TalkSettings defaults, with any JACK_<FIELD> environment variable overriding that field.

    For example JACK_MOUTH_GAIN_DB=20.
    """
    for field, key in MOVED_TO_POSES.items():
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise SystemExit(
                f"{var} moved to the mouth's entry in /etc/jack/poses.toml as {key}; "
                "remove it from /etc/jack/jack.env, then: sudo systemctl restart jack"
            )
    for field in REPLACED_BY_MOUTH_GAIN:
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise SystemExit(
                f"{var} is gone: lip sync now follows the audio's amplitude. Remove it from /etc/jack/jack.env, "
                "tune JACK_MOUTH_GAIN_DB instead, then: sudo systemctl restart jack"
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


def check_mouth_settings(settings: TalkSettings, profiles: Mapping[str, MotorProfile]) -> None:
    """Lip sync's gain against the mouth's volts, or a one-line exit naming the problem."""
    try:
        check_mouth_gain(settings, profiles["mouth"])
    except ValueError as error:
        raise SystemExit(
            "Mouth settings are invalid (JACK_MOUTH_GAIN_DB in /etc/jack/jack.env, or the [mouth] min_v/max_v "
            f"in /etc/jack/poses.toml): {error}"
        ) from error


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


@dataclass(frozen=True)
class RocConfig:
    source_port: int
    repair_port: int
    control_port: int
    target_latency_ms: float


def roc_config(environ: Mapping[str, str] = os.environ) -> RocConfig:
    """roc-recv's ports and latency target, from jack.env or their defaults."""
    latency = _env_number(environ, "JACK_ROC_TARGET_LATENCY_MS", DEFAULT_ROC_TARGET_LATENCY_MS)
    if latency <= 0:
        raise SystemExit(f"JACK_ROC_TARGET_LATENCY_MS={latency!r} in /etc/jack/jack.env must be positive")
    return RocConfig(
        source_port=_env_port(environ, "JACK_ROC_SOURCE_PORT", DEFAULT_ROC_SOURCE_PORT),
        repair_port=_env_port(environ, "JACK_ROC_REPAIR_PORT", DEFAULT_ROC_REPAIR_PORT),
        control_port=_env_port(environ, "JACK_ROC_CONTROL_PORT", DEFAULT_ROC_CONTROL_PORT),
        target_latency_ms=latency,
    )


def start_roc_voice(config: RocConfig, max_backlog_frames: int) -> RocVoice:
    """roc-recv running and feeding a RocVoice, or a one-line exit saying what to install."""
    command = roc_recv_command(config.source_port, config.repair_port, config.control_port, config.target_latency_ms)
    voice = RocVoice(command, max_backlog_frames, print)
    try:
        voice.start()
    except FileNotFoundError as error:
        raise SystemExit("roc-recv not found: sudo apt install roc-toolkit-tools") from error
    return voice


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


def build_motors(bus: I2CBus) -> dict[str, Tb6612Motor]:
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
    check_mouth_settings(settings, profiles)
    config = show_control_config()
    roc = roc_config()
    if overrides := describe_overrides(settings):
        print(f"Mouth setting overrides: {overrides}")  # noqa: T201
    board = ControlBoard(profiles, config.timeout_s, config.mouth_mode, time.monotonic)
    voice = MumbleVoice(settings.max_backlog_frames, print)
    mumble_client = connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, mumble_password())
    roc_voice = start_roc_voice(roc, settings.max_backlog_frames)
    try:
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
            print(  # noqa: T201
                f"Talking: ROC (UDP {roc.source_port}/{roc.repair_port}/{roc.control_port}, "
                f"{roc.target_latency_ms:g} ms) and Mumble voice on {ALSA_DEVICE}; "
                f"motors {', '.join(motors)} on {SUPPLY_VOLTS} V; "
                f"mouth {config.mouth_mode}; OSC UDP {config.osc_port}, HTTP {config.http_port}; "
                f"OSC replies to {reply_target}"
                + (f"; uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
            )
            notify("READY=1")
            run_talk_loop(
                [voice, roc_voice], sink, motors, profiles, settings, board, SUPPLY_VOLTS,
                watchdog_noting_mumble(mumble_client, print),
            )
    finally:
        roc_voice.close()


if __name__ == "__main__":
    main()
