"""Motor Driver HAT test: motor B steps -6 V (0.5 s), -3 V (1 s), 0 V (2 s) on repeat; motor A stays off."""

import signal
import time
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from motor_test.pca9685 import Pca9685
from motor_test.ports import MotorOutput
from motor_test.ramp import constant_profile, hold_sequence_profile
from motor_test.smoke_test import run_profiles_loop
from motor_test.systemd_notify import notify
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQ_HZ = 50

SUPPLY_VOLTS = 12.0
STEP_S = 0.05
# (volts, seconds) held in order, then repeated.
MOTOR_B_SEQUENCE = ((-6.0, 0.5), (-3.0, 1.0), (0.0, 2.0))


def exit_on_sigterm(signum: int, frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so run_profiles_loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the ramp loop completed another cycle."""
    notify("WATCHDOG=1")


def build_profiles(chip: Pca9685) -> list[tuple[MotorOutput, list[int]]]:
    """Motor B plays MOTOR_B_SEQUENCE; motor A holds 0 V for the same cycle length."""
    motor_b_counts = hold_sequence_profile(MOTOR_B_SEQUENCE, SUPPLY_VOLTS, STEP_S)
    return [
        (Tb6612Motor(chip, MOTOR_A), constant_profile(0.0, SUPPLY_VOLTS, len(motor_b_counts))),
        (Tb6612Motor(chip, MOTOR_B), motor_b_counts),
    ]


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        profiles = build_profiles(chip)
        steps = ", ".join(f"{volts:+} V for {seconds} s" for volts, seconds in MOTOR_B_SEQUENCE)
        print(f"Looping on {SUPPLY_VOLTS} V supply: motor A off, motor B {steps}")
        notify("READY=1")
        run_profiles_loop(profiles, STEP_S, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
