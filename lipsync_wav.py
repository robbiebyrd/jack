"""Tune Jack's lip sync by eye: plays a WAV through the talk loop on the Pi, every setting overridable.

Stop the app first so the two programs don't fight over the HAT and the sound card:
    sudo systemctl stop jack
    sudo -u jack /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py voice.wav --mouth-gain-db 20 --release-s 0.1
The WAV must be mono 16-bit 48 kHz. Settings are in jack/show/audio/talk_settings.py;
the mouth's voltages are in poses.toml. Run it as jack: only jack can read the overrides in /etc/jack.
"""

import argparse
import dataclasses
import sys
import time
from collections.abc import Callable

from smbus2 import SMBus

from jack.adapters.audio.alsa_sink import open_alsa_sink
from jack.adapters.audio.wav_source import WavSource
from jack.adapters.hardware.motor_hats import build_motors
from jack.adapters.system.deployment import ALSA_DEVICE, ALSA_PERIODS, I2C_BUS, POSES_PATHS, PWM_FREQ_HZ
from jack.adapters.system.service_guard import STOP_APP_FIRST, app_is_running, tool_error_message
from jack.application.config import DEFAULT_CONTROL_TIMEOUT_S
from jack.application.talk_loop import run_talk_loop
from jack.show.audio.lip_sync import check_mouth_gain
from jack.show.audio.pcm import TICKS_PER_SECOND
from jack.show.audio.talk_settings import TalkSettings
from jack.show.control.control_board import ControlBoard
from jack.show.motion.motors import SUPPLY_VOLTS
from jack.show.motion.poses import load_profiles

# Keep running this long after the WAV ends so the mouth closes before the motors brake.
TAIL_TICKS = TICKS_PER_SECOND


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play a WAV through Jack's talk loop to tune the lip sync.")
    parser.add_argument("wav", help="mono 16-bit 48 kHz WAV file")
    defaults = TalkSettings()
    for field in dataclasses.fields(TalkSettings):
        default = getattr(defaults, field.name)
        parser.add_argument(f"--{field.name.replace('_', '-')}", type=float, default=default, help=f"default {default}")
    return parser


def settings_from_args(args: argparse.Namespace) -> TalkSettings:
    return TalkSettings(**{field.name: getattr(args, field.name) for field in dataclasses.fields(TalkSettings)})


def after_tail(source: WavSource, tail_ticks: int) -> Callable[[], bool]:
    """An `until` check for the talk loop: true once `tail_ticks` ticks have run after the source finished."""
    remaining = tail_ticks

    def done() -> bool:
        nonlocal remaining
        if source.finished:
            if remaining == 0:
                return True
            remaining -= 1
        return False

    return done


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if app_is_running():
        print(STOP_APP_FIRST, file=sys.stderr)
        return 1
    try:
        settings = settings_from_args(args)
        profiles = load_profiles(POSES_PATHS, SUPPLY_VOLTS)
        check_mouth_gain(settings, profiles["mouth"])
        source = WavSource(args.wav)
    except (ValueError, OSError) as error:
        print(tool_error_message(error), file=sys.stderr)
        return 2
    print(f"Playing {args.wav} on {ALSA_DEVICE} with {settings}")
    with SMBus(I2C_BUS) as bus:
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        try:
            motors = build_motors(bus, PWM_FREQ_HZ)
        except OSError as error:
            print(tool_error_message(error), file=sys.stderr)
            return 2
        try:
            run_talk_loop(
                [source],
                sink,
                motors,
                profiles,
                settings,
                ControlBoard(profiles, DEFAULT_CONTROL_TIMEOUT_S, "live", time.monotonic),
                SUPPLY_VOLTS,
                lambda: None,
                after_tail(source, TAIL_TICKS),
            )
        except KeyboardInterrupt:
            print("\nstopped; motors braked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
