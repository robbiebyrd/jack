"""Jack's four motors on their two Waveshare HATs, built from the wiring table in motors.py."""

from jack.adapters.hardware.pca9685 import I2CBus, Pca9685
from jack.adapters.hardware.tb6612_motor import MOTOR_CHANNELS, Tb6612Motor
from jack.show.motion.motors import MOTORS
from jack.support.attempt_all import attempt_all


def build_motors(bus: I2CBus, pwm_freq_hz: float) -> dict[str, Tb6612Motor]:
    """Every motor on its HAT and channel, braked as soon as its HAT is up.

    A crash without cleanup can leave a HAT driving at its last duty, so each HAT's motors are
    braked before the next HAT is tried. A HAT that doesn't answer raises OSError naming its address.
    """
    motors: dict[str, Tb6612Motor] = {}
    for address in dict.fromkeys(spec.address for spec in MOTORS):
        try:
            chip = Pca9685(bus, address, pwm_freq_hz)
            hat_motors = {
                spec.name: Tb6612Motor(chip, MOTOR_CHANNELS[spec.channel]) for spec in MOTORS if spec.address == address
            }
            attempt_all(motor.stop for motor in hat_motors.values())
        except OSError as error:
            raise OSError(
                f"Motor HAT at {address:#04x} is not responding ({error}); check it is seated and its address pads"
            ) from error
        motors.update(hat_motors)
    return motors
