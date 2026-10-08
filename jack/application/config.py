"""Jack's settings from the environment (/etc/jack/jack.env on the Pi) and poses.toml, checked before anything moves.

Every reader takes the environment as a mapping, so the composition roots pass `os.environ` and tests
pass a dict. A bad or outdated setting raises ConfigError, whose message names the variable, the file
it lives in and what to do; the app turns that into a one-line exit.
"""

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from jack.show.audio.lip_sync import check_mouth_gain
from jack.show.audio.talk_settings import TalkSettings
from jack.show.control.control_board import MOUTH_MODES
from jack.show.motion.poses import MotorProfile, load_profiles

ENV_FILE = "/etc/jack/jack.env"
POSES_FILE = "/etc/jack/poses.toml"
RESTART = "then: sudo systemctl restart jack"

MUMBLE_PASSWORD_ENV = "JACK_MUMBLE_PASSWORD"
# Any TalkSettings field can be overridden in jack.env as JACK_<FIELD>.
SETTING_ENV_PREFIX = "JACK_"
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
# Show control (SPEC.md "Show control"); every value can be set in jack.env.
DEFAULT_OSC_PORT = 9000
DEFAULT_HTTP_PORT = 8080
# Longer than the longest hold (the pivot's 4 s): TouchOSC doesn't resend a fader held still, and its
# faders send rest themselves on release, so this only covers a lost network or a crashed controller.
DEFAULT_CONTROL_TIMEOUT_S = 5.0
# Roc Toolkit voice (SPEC.md "ROC voice"); every value can be set in jack.env.
DEFAULT_ROC_SOURCE_PORT = 10001
DEFAULT_ROC_REPAIR_PORT = 10002
DEFAULT_ROC_CONTROL_PORT = 10003
# The spike's lowest steady target over the Wi-Fi LAN was 90 ms; 100 ms leaves a margin.
DEFAULT_ROC_TARGET_LATENCY_MS = 100.0


class ConfigError(ValueError):
    """A setting that stops the app before it touches hardware; the message says which, and how to fix it."""


@dataclass(frozen=True)
class ShowControlConfig:
    osc_port: int
    http_port: int
    timeout_s: float
    mouth_mode: str
    reply_port: int | None = None


@dataclass(frozen=True)
class RocConfig:
    source_port: int
    repair_port: int
    control_port: int
    target_latency_ms: float


@dataclass(frozen=True)
class AppConfig:
    """Everything main.py reads before it touches hardware."""

    settings: TalkSettings
    profiles: Mapping[str, MotorProfile]
    show_control: ShowControlConfig
    roc: RocConfig
    mumble_password: str


def load_app_config(environ: Mapping[str, str], poses_paths: Sequence[Path], supply_volts: float) -> AppConfig:
    """Every setting the app needs, validated together; raises ConfigError for the first problem found."""
    settings = talk_settings(environ)
    profiles = motor_profiles(poses_paths, supply_volts)
    check_mouth_settings(settings, profiles)
    return AppConfig(
        settings=settings,
        profiles=profiles,
        show_control=show_control_config(environ),
        roc=roc_config(environ),
        mumble_password=mumble_password(environ),
    )


def mumble_password(environ: Mapping[str, str]) -> str:
    """The Mumble server password, which systemd loads from jack.env."""
    password = environ.get(MUMBLE_PASSWORD_ENV, "")
    if not password:
        raise ConfigError(
            f"{MUMBLE_PASSWORD_ENV} is empty or unset. Put the Mumble server password in {ENV_FILE}, {RESTART}"
        )
    return password


def talk_settings(environ: Mapping[str, str]) -> TalkSettings:
    """TalkSettings defaults, with any JACK_<FIELD> environment variable overriding that field.

    For example JACK_MOUTH_GAIN_DB=20. Variables for settings that moved to poses.toml, or that the
    amplitude lip sync replaced, are refused with a message saying where they went.
    """
    for field, key in MOVED_TO_POSES.items():
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise ConfigError(
                f"{var} moved to the mouth's entry in {POSES_FILE} as {key}; remove it from {ENV_FILE}, {RESTART}"
            )
    for field in REPLACED_BY_MOUTH_GAIN:
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise ConfigError(
                f"{var} is gone: lip sync now follows the audio's amplitude. Remove it from {ENV_FILE}, "
                f"tune JACK_MOUTH_GAIN_DB instead, {RESTART}"
            )
    overrides: dict[str, float] = {}
    for field in dataclasses.fields(TalkSettings):
        var = SETTING_ENV_PREFIX + field.name.upper()
        value = environ.get(var, "")
        if value:
            overrides[field.name] = _parse_number(var, value)
    try:
        return TalkSettings(**overrides)
    except ValueError as error:
        raise ConfigError(f"Mouth settings from {ENV_FILE} are invalid: {error}") from error


def describe_overrides(settings: TalkSettings) -> str:
    """The fields that differ from the defaults, as 'name=value, ...'; empty when there are none."""
    defaults = TalkSettings()
    return ", ".join(
        f"{field.name}={getattr(settings, field.name)}"
        for field in dataclasses.fields(TalkSettings)
        if getattr(settings, field.name) != getattr(defaults, field.name)
    )


def motor_profiles(paths: Sequence[Path], supply_volts: float) -> dict[str, MotorProfile]:
    """Every motor's profile from the poses files, or a ConfigError naming the problem."""
    try:
        return load_profiles(paths, supply_volts)
    except (ValueError, OSError) as error:
        raise ConfigError(f"Motor settings are invalid: {error}") from error


def check_mouth_settings(settings: TalkSettings, profiles: Mapping[str, MotorProfile]) -> None:
    """Lip sync's gain against the mouth's volts, or a ConfigError naming both places it can be fixed."""
    try:
        check_mouth_gain(settings, profiles["mouth"])
    except ValueError as error:
        raise ConfigError(
            f"Mouth settings are invalid (JACK_MOUTH_GAIN_DB in {ENV_FILE}, or the [mouth] min_v/max_v "
            f"in {POSES_FILE}): {error}"
        ) from error


def show_control_config(environ: Mapping[str, str]) -> ShowControlConfig:
    """Ports, dead-man timeout and startup mouth mode, from jack.env or their defaults."""
    mode = environ.get("JACK_MOUTH_MODE") or "live"
    if mode not in MOUTH_MODES:
        raise ConfigError(f"JACK_MOUTH_MODE={mode!r} in {ENV_FILE} must be one of {', '.join(MOUTH_MODES)}")
    timeout = _env_number(environ, "JACK_CONTROL_TIMEOUT_S", DEFAULT_CONTROL_TIMEOUT_S)
    if timeout <= 0:
        raise ConfigError(f"JACK_CONTROL_TIMEOUT_S={timeout!r} in {ENV_FILE} must be positive")
    return ShowControlConfig(
        osc_port=_env_port(environ, "JACK_OSC_PORT") or DEFAULT_OSC_PORT,
        http_port=_env_port(environ, "JACK_HTTP_PORT") or DEFAULT_HTTP_PORT,
        timeout_s=timeout,
        mouth_mode=mode,
        reply_port=_env_port(environ, "JACK_OSC_REPLY_PORT"),
    )


def roc_config(environ: Mapping[str, str]) -> RocConfig:
    """roc-recv's ports and latency target, from jack.env or their defaults."""
    latency = _env_number(environ, "JACK_ROC_TARGET_LATENCY_MS", DEFAULT_ROC_TARGET_LATENCY_MS)
    if latency <= 0:
        raise ConfigError(f"JACK_ROC_TARGET_LATENCY_MS={latency!r} in {ENV_FILE} must be positive")
    return RocConfig(
        source_port=_env_port(environ, "JACK_ROC_SOURCE_PORT") or DEFAULT_ROC_SOURCE_PORT,
        repair_port=_env_port(environ, "JACK_ROC_REPAIR_PORT") or DEFAULT_ROC_REPAIR_PORT,
        control_port=_env_port(environ, "JACK_ROC_CONTROL_PORT") or DEFAULT_ROC_CONTROL_PORT,
        target_latency_ms=latency,
    )


def _env_number(environ: Mapping[str, str], var: str, default: float) -> float:
    """`var` as a finite number, or `default` when it is unset or empty."""
    value = environ.get(var, "")
    return _parse_number(var, value) if value else default


def _parse_number(var: str, value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise ConfigError(f"{var}={value!r} in {ENV_FILE} is not a number")
    return number


def _env_port(environ: Mapping[str, str], var: str) -> int | None:
    """`var` as a port number, or None when it is unset or empty (ports start at 1, so `or default` is safe)."""
    value = environ.get(var, "")
    if not value:
        return None
    if not (value.isascii() and value.isdigit()) or not 1 <= int(value) <= 65535:
        raise ConfigError(f"{var}={value!r} in {ENV_FILE} must be a port number 1-65535")
    return int(value)
