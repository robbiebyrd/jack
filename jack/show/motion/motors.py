"""Jack's four motors by name: which HAT and channel each is wired to."""

from dataclasses import dataclass

# Both HATs share the 12 V motor supply (SPEC.md "Hardware facts"); every voltage is checked against it.
SUPPLY_VOLTS = 12.0
HAT1_ADDRESS = 0x40
# The second HAT's bridged pad behaves as A0, so it answers at 0x41 (SPEC.md "Hardware facts").
HAT2_ADDRESS = 0x41


@dataclass(frozen=True)
class MotorSpec:
    name: str
    address: int
    channel: str
    # True when commands run -1…1 (pivot: left…right; hand: open…curled) instead of 0…1.
    two_sided: bool


MOTORS: tuple[MotorSpec, ...] = (
    MotorSpec("mouth", HAT1_ADDRESS, "A", two_sided=False),
    MotorSpec("hand", HAT1_ADDRESS, "B", two_sided=True),
    MotorSpec("pivot", HAT2_ADDRESS, "A", two_sided=True),
    MotorSpec("elbow", HAT2_ADDRESS, "B", two_sided=False),
)
MOTOR_NAMES: tuple[str, ...] = tuple(spec.name for spec in MOTORS)


def motor_spec(name: str) -> MotorSpec:
    for spec in MOTORS:
        if spec.name == name:
            return spec
    raise ValueError(f"unknown motor {name!r}; expected one of {', '.join(MOTOR_NAMES)}")
