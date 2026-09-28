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

    def drive(self, count):
        self.calls.append(("drive", count))

    def stop(self):
        self.calls.append(("stop",))


def make_fake_leds(leds_dir):
    """Build a /sys/class/leds stand-in with the Pi 4's ACT and PWR LEDs and their normal triggers."""
    for name, trigger in (("ACT", "mmc0"), ("PWR", "default-on")):
        led = leds_dir / name
        led.mkdir(parents=True)
        # Real trigger files list every trigger, bracketing the active one.
        (led / "trigger").write_text(f"none {'[' + trigger + ']'} timer heartbeat\n")
    return leds_dir


def install_recording_sleep(bin_dir, sleep_log, leds_during_sleep):
    """Put a `sleep` on PATH that logs its arguments and snapshots both LED triggers instead of waiting.

    Scripts using it need SLEEP_LOG, LEDS_DURING_SLEEP and JACK_LEDS_DIR in their environment;
    SLEEP_EXIT, when set, becomes its exit status.
    """
    sleep = bin_dir / "sleep"
    sleep.write_text(
        "#!/bin/sh\n"
        'echo "$@" >> "$SLEEP_LOG"\n'
        'cat "$JACK_LEDS_DIR/ACT/trigger" "$JACK_LEDS_DIR/PWR/trigger" > "$LEDS_DURING_SLEEP"\n'
        'exit "${SLEEP_EXIT:-0}"\n'
    )
    sleep.chmod(0o755)
    return {"SLEEP_LOG": str(sleep_log), "LEDS_DURING_SLEEP": str(leds_during_sleep)}
