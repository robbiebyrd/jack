"""Interactive calibration for motor B (the mouth): type '<volts> <seconds>', it moves, then brakes.

Stop the app first so the two programs don't fight over the HAT:
    sudo systemctl stop jack
    python3 /opt/jack/calibrate.py
"""

import subprocess
import sys
import time

from smbus2 import SMBus

from main import I2C_BUS, PCA9685_ADDRESS, PWM_FREQ_HZ, STEP_S, SUPPLY_VOLTS
from motor_test.calibration import MAX_MOVE_S, USAGE, run_calibration
from motor_test.pca9685 import Pca9685
from motor_test.tb6612_motor import MOTOR_B, Tb6612Motor

APP_SERVICE = "jack.service"


def app_is_running() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", APP_SERVICE], check=False).returncode == 0


def main() -> int:
    if app_is_running():
        print(f"{APP_SERVICE} is running and driving the HAT. Stop it first: sudo systemctl stop jack", file=sys.stderr)
        return 1
    print(f"Motor B calibration on {SUPPLY_VOLTS} V supply. Negative volts open the mouth.")
    print(f"{USAGE}. Moves run in {STEP_S} s steps, are capped at {MAX_MOVE_S} s and always end braked.")
    with SMBus(I2C_BUS) as bus:
        motor = Tb6612Motor(Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ), MOTOR_B)
        try:
            run_calibration(motor, SUPPLY_VOLTS, STEP_S, lambda: input("b> "), print, time.sleep)
        except KeyboardInterrupt:
            print("\nbraked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
