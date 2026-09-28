"""Tune Jack's lip sync by eye: plays a WAV through the talk loop on the Pi, every setting overridable.

Stop the app first so the two programs don't fight over the HAT and the sound card:
    sudo systemctl stop jack
    /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py voice.wav --gate-open-db -30 --release-s 0.1
The WAV must be mono 16-bit 48 kHz. Settings are in motor_test/talk_settings.py.
"""

import argparse
import dataclasses
import sys
from collections.abc import Callable

from smbus2 import SMBus

from main import ALSA_DEVICE, ALSA_PERIODS, I2C_BUS, PCA9685_ADDRESS, PWM_FREQ_HZ, SUPPLY_VOLTS
from motor_test.alsa_sink import open_alsa_sink
from motor_test.pca9685 import Pca9685
from motor_test.pcm import TICKS_PER_SECOND
from motor_test.service_guard import STOP_APP_FIRST, app_is_running
from motor_test.talk_loop import check_supply, run_talk_loop
from motor_test.talk_settings import TalkSettings
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor
from motor_test.wav_source import WavSource

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


def after_tail(source, tail_ticks: int) -> Callable[[], bool]:
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
        check_supply(settings, SUPPLY_VOLTS)
        source = WavSource(args.wav)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Playing {args.wav} on {ALSA_DEVICE} with {settings}")
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        try:
            run_talk_loop(
                [source],
                sink,
                Tb6612Motor(chip, MOTOR_B),
                [Tb6612Motor(chip, MOTOR_A)],
                settings,
                SUPPLY_VOLTS,
                lambda: None,
                after_tail(source, TAIL_TICKS),
            )
        except KeyboardInterrupt:
            print("\nstopped; motors braked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
