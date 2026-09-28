"""Motor Driver HAT smoke test: loops motor A through 0 -> +6 V -> 0 and motor B through 0 -> -6 V -> 0."""

import signal
import time
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from motor_test.motor_group import MotorGroup
from motor_test.pca9685 import Pca9685
from motor_test.ramp import ramp_profile
from motor_test.smoke_test import run_ramp_loop
from motor_test.systemd_notify import notify
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQ_HZ = 50

SUPPLY_VOLTS = 12.0
PEAK_VOLTS = 6.0
CYCLE_S = 2.0
STEPS_PER_CYCLE = 50


def exit_on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so run_ramp_loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the ramp loop completed another cycle."""
    notify("WATCHDOG=1")


def build_motors(chip: Pca9685) -> MotorGroup:
    """Motor A ramps positive; motor B is reversed so its terminals ramp negative."""
    return MotorGroup([Tb6612Motor(chip, MOTOR_A), Tb6612Motor(chip, MOTOR_B, reverse=True)])


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    counts = ramp_profile(PEAK_VOLTS, SUPPLY_VOLTS, STEPS_PER_CYCLE)
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        motors = build_motors(chip)
        print(f"Looping motor A 0 -> +{PEAK_VOLTS} V -> 0 and motor B 0 -> -{PEAK_VOLTS} V -> 0 every {CYCLE_S} s on {SUPPLY_VOLTS} V supply")
        notify("READY=1")
        run_ramp_loop(motors, counts, CYCLE_S / STEPS_PER_CYCLE, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
