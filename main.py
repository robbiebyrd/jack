"""Motor Driver HAT smoke test: loops motor B through a 0 -> 6 V -> 0 V ramp."""

import signal
import time

from smbus2 import SMBus

from motor_test.pca9685_motor import Pca9685Motor
from motor_test.ramp import ramp_profile
from motor_test.smoke_test import run_ramp_loop
from motor_test.systemd_notify import notify

I2C_BUS = 1
PCA9685_ADDRESS = 0x40
PWM_FREQ_HZ = 50
MOTOR_B_PWM_CHANNEL = 5
MOTOR_B_IN1_CHANNEL = 3
MOTOR_B_IN2_CHANNEL = 4

SUPPLY_VOLTS = 12.0
PEAK_VOLTS = 6.0
CYCLE_S = 2.0
STEPS_PER_CYCLE = 50


def exit_on_sigterm(signum, frame):
    """Turn SIGTERM into SystemExit so run_ramp_loop's cleanup stops the motor."""
    raise SystemExit(0)


def ping_watchdog():
    notify("WATCHDOG=1")


def main():
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    counts = ramp_profile(PEAK_VOLTS, SUPPLY_VOLTS, STEPS_PER_CYCLE)
    with SMBus(I2C_BUS) as bus:
        motor = Pca9685Motor(
            bus,
            PCA9685_ADDRESS,
            MOTOR_B_PWM_CHANNEL,
            MOTOR_B_IN1_CHANNEL,
            MOTOR_B_IN2_CHANNEL,
            PWM_FREQ_HZ,
        )
        print(f"Looping motor B 0 -> {PEAK_VOLTS} V -> 0 every {CYCLE_S} s on {SUPPLY_VOLTS} V supply")
        notify("READY=1")
        run_ramp_loop(motor, counts, CYCLE_S / STEPS_PER_CYCLE, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
