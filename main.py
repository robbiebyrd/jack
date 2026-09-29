"""Jack talks: Boss's voice from Mumble plays on the 3.5 mm jack and the mouth (motor B) moves with it; motor A stays off."""

import dataclasses
import math
import os
import random
import signal
from collections.abc import Callable, Mapping
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from motor_test.alsa_sink import open_alsa_sink
from motor_test.mouth import CLOSE, RELAX, STEP_S, Segment, open_fully, rest
from motor_test.mumble_voice import MumbleVoice, connect_mumble
from motor_test.pca9685 import Pca9685
from motor_test.ramp import constant_profile, segment_profile
from motor_test.speech import random_phrase
from motor_test.systemd_notify import notify
from motor_test.talk_loop import check_supply, run_talk_loop
from motor_test.talk_settings import TalkSettings
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
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
    """One random talking phrase on the mouth, with motor A off. Counts are [motor A, motor B]."""
    return _mouth_only(random_phrase(rng))


def demo_cycle() -> list[list[int]]:
    """MOUTH_DEMO on the mouth, with motor A off. Counts are [motor A, motor B]."""
    return _mouth_only(MOUTH_DEMO)


def _mouth_only(segments: tuple[Segment, ...]) -> list[list[int]]:
    mouth = segment_profile(segments, SUPPLY_VOLTS, STEP_S)
    return [constant_profile(0.0, SUPPLY_VOLTS, len(mouth)), mouth]


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
    overrides: dict[str, float] = {}
    for field in dataclasses.fields(TalkSettings):
        var = SETTING_ENV_PREFIX + field.name.upper()
        value = environ.get(var, "")
        if not value:
            continue
        try:
            number = float(value)
        except ValueError:
            number = math.nan
        if not math.isfinite(number):
            raise SystemExit(f"{var}={value!r} in /etc/jack/jack.env is not a number")
        overrides[field.name] = number
    try:
        settings = TalkSettings(**overrides)
        check_supply(settings, SUPPLY_VOLTS)
    except ValueError as error:
        raise SystemExit(f"Mouth settings from /etc/jack/jack.env are invalid: {error}") from error
    return settings


def describe_overrides(settings: TalkSettings) -> str:
    """The fields that differ from the defaults, as 'name=value, ...'; empty when there are none."""
    defaults = TalkSettings()
    return ", ".join(
        f"{field.name}={getattr(settings, field.name)}"
        for field in dataclasses.fields(TalkSettings)
        if getattr(settings, field.name) != getattr(defaults, field.name)
    )


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    settings = talk_settings()
    if overrides := describe_overrides(settings):
        print(f"Mouth setting overrides: {overrides}")
    voice = MumbleVoice(settings.max_backlog_frames, print)
    mumble_client = connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, mumble_password())
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        print(f"Talking: Mumble voice on {ALSA_DEVICE}, mouth on motor B, {SUPPLY_VOLTS} V supply, motor A off")
        notify("READY=1")
        run_talk_loop(
            [voice],
            sink,
            Tb6612Motor(chip, MOTOR_B),
            [Tb6612Motor(chip, MOTOR_A)],
            settings,
            SUPPLY_VOLTS,
            watchdog_while_connected(mumble_client),
        )


if __name__ == "__main__":
    main()
