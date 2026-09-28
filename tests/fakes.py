"""In-memory stand-ins for hardware, shared by the test modules."""


class RecordingBus:
    """In-memory PCA9685 register file that records every byte write."""

    def __init__(self):
        self.registers = {}
        self.writes = []

    def write_byte_data(self, i2c_addr, register, value):
        self.writes.append((i2c_addr, register, value))
        self.registers[register] = value

    def read_byte_data(self, i2c_addr, register):
        return self.registers.get(register, 0)


def off_count(bus, channel):
    """Decode the 12-bit OFF count the chip holds for `channel`."""
    base = 0x06 + 4 * channel
    return bus.registers[base + 2] | (bus.registers[base + 3] << 8)


class RecordingMotor:
    """MotorOutput that records the commands it receives, in order."""

    def __init__(self):
        self.calls = []

    def set_forward(self):
        self.calls.append(("forward",))

    def set_duty(self, count):
        self.calls.append(("duty", count))

    def stop(self):
        self.calls.append(("stop",))
