"""Interactive calibration for one of Jack's motors: type '<volts> <seconds>' or a pose name; it moves, then brakes.

Stop the app first so the two programs don't fight over the HATs:
    sudo systemctl stop jack
    sudo -u jack /opt/jack-venv/bin/python /opt/jack/calibrate.py elbow
Run it as jack: only jack can read the overrides in /etc/jack.
"""

import argparse
import sys
import time

from smbus2 import SMBus

from jack.adapters.hardware.pca9685 import Pca9685
from jack.adapters.hardware.tb6612_motor import MOTOR_CHANNELS, Tb6612Motor
from jack.adapters.system.service_guard import STOP_APP_FIRST, app_is_running, tool_error_message
from jack.application.calibration import MAX_MOVE_S, USAGE, run_calibration
from jack.show.motion.motors import MOTOR_NAMES, motor_spec
from jack.show.motion.poses import load_profiles
from jack.show.motion.segments import STEP_S
from main import I2C_BUS, POSES_PATHS, PWM_FREQ_HZ, SUPPLY_VOLTS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive one of Jack's motors by hand to find its voltages.")
    parser.add_argument("motor", choices=MOTOR_NAMES)
    args = parser.parse_args(argv)
    if app_is_running():
        print(STOP_APP_FIRST, file=sys.stderr)
        return 1
    try:
        profile = load_profiles(POSES_PATHS, SUPPLY_VOLTS)[args.motor]
    except (ValueError, OSError) as error:
        print(tool_error_message(error), file=sys.stderr)
        return 2
    spec = motor_spec(args.motor)
    note = "" if profile.calibrated else " — poses are UNCALIBRATED placeholders"
    print(f"{args.motor} on HAT {spec.address:#04x} channel {spec.channel}, {SUPPLY_VOLTS} V supply{note}.")
    print(f"{USAGE}; poses: {', '.join(sorted(profile.poses))}.")
    print(f"Moves run in {STEP_S} s steps, are capped at {MAX_MOVE_S} s and always end braked.")
    with SMBus(I2C_BUS) as bus:
        motor = Tb6612Motor(Pca9685(bus, spec.address, PWM_FREQ_HZ), MOTOR_CHANNELS[spec.channel])
        try:
            run_calibration(
                motor, args.motor, profile, SUPPLY_VOLTS, STEP_S, lambda: input(f"{args.motor}> "), print, time.sleep
            )
        except KeyboardInterrupt:
            print("\nbraked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
