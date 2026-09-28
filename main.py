"""Animatronic head: the mouth (motor B) moves as if speaking, in random phrases; motor A stays off."""

import random
import signal
import time
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from motor_test.mouth import CLOSE, RELAX, STEP_S, Segment, open_fully, rest
from motor_test.pca9685 import Pca9685
from motor_test.ramp import constant_profile, segment_profile
from motor_test.smoke_test import run_generated_loop
from motor_test.speech import random_phrase
from motor_test.systemd_notify import notify
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQ_HZ = 50

SUPPLY_VOLTS = 12.0
# Fixed pose tour: an alternative to speaking, e.g. for checking the mechanism.
MOUTH_DEMO = (*CLOSE, *rest(1.5), *RELAX, *rest(1.5), *open_fully(0.5))


def exit_on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so the playback loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the playback loop completed another cycle."""
    notify("WATCHDOG=1")


def speaking_cycle(rng: random.Random) -> list[list[int]]:
    """One random talking phrase on the mouth, with motor A off. Counts are [motor A, motor B]."""
    return _mouth_only(random_phrase(rng))


def demo_cycle() -> list[list[int]]:
    """MOUTH_DEMO on the mouth, with motor A off. Counts are [motor A, motor B]."""
    return _mouth_only(MOUTH_DEMO)


def _mouth_only(segments: tuple[Segment, ...]) -> list[list[int]]:
    mouth = segment_profile(segments, SUPPLY_VOLTS, STEP_S)
    return [constant_profile(0.0, SUPPLY_VOLTS, len(mouth)), mouth]


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    rng = random.Random()
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        motors = [Tb6612Motor(chip, MOTOR_A), Tb6612Motor(chip, MOTOR_B)]
        print(f"Mouth speaking in random phrases on {SUPPLY_VOLTS} V supply, motor A off")
        notify("READY=1")
        run_generated_loop(motors, lambda: speaking_cycle(rng), STEP_S, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
