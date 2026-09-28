"""Motor Driver HAT smoke test: motor A ramps 0 -> +6 V -> 0 while motor B squares between +3 V and -3 V."""

import signal
import time
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from motor_test.pca9685 import Pca9685
from motor_test.ports import MotorOutput
from motor_test.ramp import ramp_profile, square_profile
from motor_test.smoke_test import run_profiles_loop
from motor_test.systemd_notify import notify
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQ_HZ = 50

SUPPLY_VOLTS = 12.0
MOTOR_A_PEAK_VOLTS = 6.0
MOTOR_B_HIGH_VOLTS = 3.0
MOTOR_B_LOW_VOLTS = -3.0
CYCLE_S = 2.0
STEPS_PER_CYCLE = 50


def exit_on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so run_profiles_loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the ramp loop completed another cycle."""
    notify("WATCHDOG=1")


def build_profiles(chip: Pca9685) -> list[tuple[MotorOutput, list[int]]]:
    """Motor A ramps 0 -> +6 V -> 0; motor B holds +3 V for the first half-cycle, then -3 V."""
    return [
        (Tb6612Motor(chip, MOTOR_A), ramp_profile(MOTOR_A_PEAK_VOLTS, SUPPLY_VOLTS, STEPS_PER_CYCLE)),
        (
            Tb6612Motor(chip, MOTOR_B),
            square_profile(MOTOR_B_HIGH_VOLTS, MOTOR_B_LOW_VOLTS, SUPPLY_VOLTS, STEPS_PER_CYCLE),
        ),
    ]


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        profiles = build_profiles(chip)
        print(
            f"Looping every {CYCLE_S} s on {SUPPLY_VOLTS} V supply: motor A 0 -> +{MOTOR_A_PEAK_VOLTS} V -> 0, "
            f"motor B {MOTOR_B_HIGH_VOLTS:+} V then {MOTOR_B_LOW_VOLTS:+} V"
        )
        notify("READY=1")
        run_profiles_loop(profiles, CYCLE_S / STEPS_PER_CYCLE, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
