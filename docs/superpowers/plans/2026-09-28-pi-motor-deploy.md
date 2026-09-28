# Pi Motor Smoke Test and Git-Driven Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loop a 0 → 6 V → 0 V ramp on motor B of the Waveshare Motor Driver HAT from a boot-time system service on the Pi, and redeploy automatically within about 60 s of a push to `main`.

**Architecture:** Hexagonal Python app. A pure domain module (`ramp.py`) computes duty counts, a `MotorOutput` port has a PCA9685 adapter, a small application loop (`smoke_test.py`) plays the ramp, and `main.py` wires them together. Deployment is plain systemd: an app service running as the `jack` system user, plus a root timer that runs a git polling script.

**Tech Stack:** Python 3.13 (Pi: Debian 13 trixie, aarch64), smbus2 0.4.3 (apt `python3-smbus2` on the Pi), pytest (dev only, on the Mac), bash, git, systemd.

**Spec:** `SPEC.md`

## Global Constraints

- I2C bus 1, PCA9685 address `0x40`, PWM frequency 50 Hz.
- Motor B channels: PWMB = 5, BIN1 = 3, BIN2 = 4. Forward = BIN1 low, BIN2 high.
- VIN = 12 V. Peak = 6 V, which is count 2048 of 4096.
- Cycle: 2 s total (1 s up, 1 s down), 50 steps of 40 ms, repeated back-to-back with no pause.
- On SIGTERM or any exception, motor B duty goes to 0 before exit.
- Checkout lives at `/opt/jack`, owned by root, cloned from `https://github.com/robbiebyrd/jack.git`.
- App runs as the system user `jack` (no home, `/usr/sbin/nologin`, member of `i2c`), not tied to any login.
- The updater runs as root every 60 s (`OnBootSec=60`, `OnUnitActiveSec=60`). It uses `git reset --hard origin/main`, and local Pi edits are discarded.
- The Pi uses system Python and apt packages only (no pip or venv on the Pi).
- Never push or merge to `main` without Boss's explicit approval. Boss runs every `sudo` step.

## Review Focus

1. **Peak equal to supply:** a 4096 count would set the PCA9685 "full off" bit and turn the motor *off*. Counts must cap at 4095. Covered in Task 1 and Task 3.
2. **SIGTERM during a sleep** (`systemctl stop` or a deploy restart): the motor must reach duty 0 before the process exits. Covered in Task 2 and Task 4.
3. **`git fetch` failure** (network down, GitHub unreachable): the updater exits non-zero, leaves the checkout alone, and does not restart the app. Covered in Task 5.
4. **Remote history rewritten** (force-push) or a tracked file edited by hand on the Pi: the Pi still converges exactly to `origin/main`. Covered in Task 5.
5. **A commit that changes `jack-update.sh` itself** while the running script is being rewritten by `git reset`: the run completes normally and restarts once. Covered in Task 5.

---

## File Structure

| Path | Responsibility |
|---|---|
| `motor_test/__init__.py` | Package marker (empty) |
| `motor_test/ramp.py` | Domain: triangle ramp to 12-bit duty counts |
| `motor_test/ports.py` | Port: `MotorOutput` protocol |
| `motor_test/smoke_test.py` | Application: play a ramp cycle forever, always stop the motor |
| `motor_test/pca9685_motor.py` | Adapter: one TB6612FNG channel through the PCA9685 over I2C |
| `main.py` | Composition root: constants, SIGTERM handler, wiring |
| `deploy/jack-update.sh` | Poll origin/main, reset, and restart the app on change |
| `deploy/jack.service` | App system unit |
| `deploy/jack-update.service` | Oneshot unit that runs the update script |
| `deploy/jack-update.timer` | Runs the updater every 60 s |
| `deploy/install.sh` | One-time root setup on the Pi |
| `tests/test_ramp.py`, `tests/test_smoke_test.py`, `tests/test_pca9685_motor.py`, `tests/test_main.py`, `tests/test_jack_update.py` | Tests |
| `pyproject.toml` | pytest config |
| `requirements-dev.txt` | Dev-only pins (pytest, smbus2) |
| `README.md` | Install, logs, how to stop |

---

### Task 1: Dev tooling and ramp domain

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `motor_test/__init__.py`, `motor_test/ramp.py`, `tests/test_ramp.py`
- Modify: `.gitignore`, `SPEC.md`

**Interfaces:**
- Produces: `motor_test.ramp.PWM_RESOLUTION = 4096`, `motor_test.ramp.PWM_MAX_COUNT = 4095`, `ramp_profile(peak_volts: float, supply_volts: float, steps: int) -> list[int]`

- [ ] **Step 1: Set up the dev environment**

```bash
cd /Users/robbiebyrd/jack
python3 -m venv .venv
.venv/bin/pip install pytest smbus2==0.4.3
.venv/bin/pip freeze | grep -iE '^(pytest|smbus2)==' > requirements-dev.txt
cat requirements-dev.txt
```
Expected: two lines, `pytest==<installed version>` and `smbus2==0.4.3`. smbus2 is pinned to match the Pi's apt package.

Append to `.gitignore`:
```
.venv/
__pycache__/
.pytest_cache/
```

Create `pyproject.toml`:
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

Create an empty `motor_test/__init__.py`.

- [ ] **Step 2: Write the failing tests** in `tests/test_ramp.py`

```python
import pytest

from motor_test.ramp import PWM_MAX_COUNT, ramp_profile


def test_six_volts_on_twelve_volt_supply_peaks_at_half_duty():
    assert max(ramp_profile(peak_volts=6.0, supply_volts=12.0, steps=50)) == 2048


def test_profile_has_requested_step_count():
    assert len(ramp_profile(6.0, 12.0, 50)) == 50


def test_profile_starts_at_zero_and_peaks_mid_cycle():
    counts = ramp_profile(6.0, 12.0, 50)
    assert counts[0] == 0
    assert counts[25] == 2048


def test_profile_rises_then_falls_symmetrically():
    counts = ramp_profile(6.0, 12.0, 50)
    assert counts[:26] == sorted(counts[:26])
    for k in range(1, 50):
        assert counts[k] == counts[50 - k]


def test_full_supply_peak_is_capped_below_the_full_off_bit():
    assert max(ramp_profile(12.0, 12.0, 50)) == PWM_MAX_COUNT


def test_zero_peak_produces_all_zero_counts():
    assert ramp_profile(0.0, 12.0, 50) == [0] * 50


@pytest.mark.parametrize(
    "peak_volts, supply_volts, steps",
    [
        (13.0, 12.0, 50),  # peak above supply
        (-1.0, 12.0, 50),  # negative peak
        (6.0, 0.0, 50),  # zero supply
        (6.0, -12.0, 50),  # negative supply
        (6.0, 12.0, 0),  # no steps
        (6.0, 12.0, 49),  # odd steps have no single peak sample
    ],
)
def test_invalid_inputs_are_rejected(peak_volts, supply_volts, steps):
    with pytest.raises(ValueError):
        ramp_profile(peak_volts, supply_volts, steps)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_ramp.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'motor_test.ramp'`

- [ ] **Step 4: Implement** `motor_test/ramp.py`

```python
"""Triangle voltage ramps expressed as PCA9685 12-bit PWM duty counts."""

PWM_RESOLUTION = 4096
# A count of 4096 sets the PCA9685 "full off" bit, so 4095 is the highest usable duty.
PWM_MAX_COUNT = PWM_RESOLUTION - 1


def ramp_profile(peak_volts: float, supply_volts: float, steps: int) -> list[int]:
    """Return one cycle of a 0 -> peak -> 0 triangle ramp as duty counts.

    The cycle starts at 0 and peaks at index steps // 2. It omits the closing 0
    so cycles can be played back-to-back without repeating a sample.
    """
    if supply_volts <= 0:
        raise ValueError(f"supply_volts must be positive, got {supply_volts}")
    if not 0 <= peak_volts <= supply_volts:
        raise ValueError(f"peak_volts must be between 0 and {supply_volts}, got {peak_volts}")
    if steps < 2 or steps % 2:
        raise ValueError(f"steps must be an even number >= 2, got {steps}")

    peak_count = min(round(peak_volts / supply_volts * PWM_RESOLUTION), PWM_MAX_COUNT)
    return [round(peak_count * (1 - abs(2 * i / steps - 1))) for i in range(steps)]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ramp.py -v`
Expected: all PASS, with no warnings.

- [ ] **Step 6: Bring `SPEC.md` in line with the signature**

In `SPEC.md`, replace the `ramp_profile` bullet under "Code structure" with:
```
- `motor_test/ramp.py` (domain, pure): `ramp_profile(peak_volts,
  supply_volts, steps)` returns one cycle of 12-bit duty counts for a
  0 → peak → 0 triangle ramp. The cycle starts at 0, peaks at
  `steps // 2`, and omits the closing 0 so cycles chain seamlessly. It
  raises `ValueError` for a non-positive supply, a peak outside
  0..supply, or an odd or too-small step count. The peak is capped at
  4095 (4096 would set the PCA9685 full-off bit).
```
In the "Testing" section, replace the ramp line with:
```
  - Ramp starts at 0, peaks at 2048 for 6 V/12 V at index `steps // 2`,
    is symmetric, and has the requested number of steps. A full-supply
    peak caps at 4095.
```

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml requirements-dev.txt motor_test/__init__.py motor_test/ramp.py tests/test_ramp.py SPEC.md
git commit -m "Add triangle ramp domain with pytest tooling"
```

---

### Task 2: MotorOutput port and ramp loop

**Files:**
- Create: `motor_test/ports.py`, `motor_test/smoke_test.py`, `tests/test_smoke_test.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime. Counts are passed in.
- Produces: `motor_test.ports.MotorOutput` (Protocol with `set_forward() -> None`, `set_duty(count: int) -> None`, `stop() -> None`) and `motor_test.smoke_test.run_ramp_loop(motor: MotorOutput, counts: Sequence[int], step_s: float, sleep: Callable[[float], None]) -> None`, which never returns normally.

- [ ] **Step 1: Write the failing tests** in `tests/test_smoke_test.py`

```python
import pytest

from motor_test.smoke_test import run_ramp_loop


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


class LoopEnded(Exception):
    pass


def sleep_that_raises_after(n, exception=LoopEnded):
    """Return a sleep function and its log. The nth call raises `exception`."""
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) >= n:
            raise exception

    return sleep, slept


def test_sets_forward_before_driving():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 10], step_s=0.04, sleep=sleep)
    assert motor.calls[0] == ("forward",)
    assert motor.calls[1] == ("duty", 0)


def test_plays_counts_in_order_and_repeats_the_cycle():
    motor = RecordingMotor()
    counts = [0, 5, 9, 5]
    sleep, _ = sleep_that_raises_after(len(counts) * 2)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, counts, step_s=0.04, sleep=sleep)
    duties = [call[1] for call in motor.calls if call[0] == "duty"]
    assert duties == counts * 2


def test_waits_one_step_after_each_count():
    motor = RecordingMotor()
    sleep, slept = sleep_that_raises_after(6)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep)
    assert slept == [0.04] * 6


def test_stops_motor_when_loop_is_interrupted_by_an_error():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(3)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep)
    assert motor.calls[-1] == ("stop",)


def test_stops_motor_on_system_exit_from_sigterm():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(2, exception=SystemExit(0))
    with pytest.raises(SystemExit):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep)
    assert motor.calls[-1] == ("stop",)


def test_empty_cycle_is_rejected_without_touching_the_motor():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(ValueError):
        run_ramp_loop(motor, [], step_s=0.04, sleep=sleep)
    assert motor.calls == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_smoke_test.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'motor_test.smoke_test'`

- [ ] **Step 3: Implement** `motor_test/ports.py`

```python
"""Ports the smoke test depends on."""

from typing import Protocol


class MotorOutput(Protocol):
    """One DC motor output driven by a PWM duty count."""

    def set_forward(self) -> None: ...

    def set_duty(self, count: int) -> None: ...

    def stop(self) -> None: ...
```

and `motor_test/smoke_test.py`:

```python
"""Plays a duty-count ramp on a motor output until the process is stopped."""

from collections.abc import Callable, Sequence

from motor_test.ports import MotorOutput


def run_ramp_loop(
    motor: MotorOutput,
    counts: Sequence[int],
    step_s: float,
    sleep: Callable[[float], None],
) -> None:
    """Drive `motor` forward through `counts` cycle after cycle, forever.

    The motor is always stopped on the way out, whether the loop ends through
    an error, KeyboardInterrupt, or SystemExit raised by a SIGTERM handler.
    """
    if not counts:
        raise ValueError("counts must contain at least one duty count")

    try:
        motor.set_forward()
        while True:
            for count in counts:
                motor.set_duty(count)
                sleep(step_s)
    finally:
        motor.stop()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS (Task 1 and Task 2 tests).

- [ ] **Step 5: Commit**

```bash
git add motor_test/ports.py motor_test/smoke_test.py tests/test_smoke_test.py
git commit -m "Add MotorOutput port and ramp loop that always stops the motor"
```

---

### Task 3: PCA9685 motor adapter

**Files:**
- Create: `motor_test/pca9685_motor.py`, `tests/test_pca9685_motor.py`

**Interfaces:**
- Consumes: `motor_test.ramp.PWM_RESOLUTION`, `motor_test.ramp.PWM_MAX_COUNT`
- Produces: `motor_test.pca9685_motor.Pca9685Motor(bus: I2CBus, address: int, pwm_channel: int, in1_channel: int, in2_channel: int, pwm_freq_hz: float)`, which satisfies `MotorOutput`. `I2CBus` is a Protocol with smbus2's `write_byte_data(i2c_addr, register, value)` and `read_byte_data(i2c_addr, register) -> int`.

Register math (PCA9685 datasheet as used by Waveshare's `PCA9685.py`): channel `n` registers start at `0x06 + 4n` (ON_L, ON_H, OFF_L, OFF_H). Motor B: PWMB channel 5 is at 0x1A–0x1D, BIN1 channel 3 at 0x12–0x15, BIN2 channel 4 at 0x16–0x19. Prescale at 50 Hz is `floor(25e6 / 4096 / 50 - 1 + 0.5) = 121`.

- [ ] **Step 1: Write the failing tests** in `tests/test_pca9685_motor.py`

```python
import pytest

from motor_test.pca9685_motor import Pca9685Motor

ADDRESS = 0x40


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


def motor_b(bus):
    return Pca9685Motor(bus, ADDRESS, pwm_channel=5, in1_channel=3, in2_channel=4, pwm_freq_hz=50)


def test_init_resets_mode_and_sets_50hz_prescale_like_waveshare():
    bus = RecordingBus()
    motor_b(bus)
    assert bus.writes == [
        (ADDRESS, 0x00, 0x00),  # MODE1 reset
        (ADDRESS, 0x00, 0x10),  # sleep to change prescale
        (ADDRESS, 0xFE, 121),  # prescale for 50 Hz
        (ADDRESS, 0x00, 0x00),  # wake
        (ADDRESS, 0x00, 0x80),  # restart
    ]


def test_set_duty_writes_off_count_to_pwmb_channel():
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    motor.set_duty(2048)
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x08),
    ]


def test_set_forward_drives_bin1_low_and_bin2_high():
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    motor.set_forward()
    assert bus.writes == [
        (ADDRESS, 0x12, 0x00),
        (ADDRESS, 0x13, 0x00),
        (ADDRESS, 0x14, 0x00),
        (ADDRESS, 0x15, 0x00),
        (ADDRESS, 0x16, 0x00),
        (ADDRESS, 0x17, 0x00),
        (ADDRESS, 0x18, 0xFF),
        (ADDRESS, 0x19, 0x0F),
    ]


def test_stop_sets_pwmb_duty_to_zero():
    bus = RecordingBus()
    motor = motor_b(bus)
    motor.set_duty(2048)
    bus.writes.clear()
    motor.stop()
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x00),
    ]


@pytest.mark.parametrize("count", [-1, 4096])
def test_out_of_range_duty_is_rejected_without_writing(count):
    bus = RecordingBus()
    motor = motor_b(bus)
    bus.writes.clear()
    with pytest.raises(ValueError):
        motor.set_duty(count)
    assert bus.writes == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pca9685_motor.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'motor_test.pca9685_motor'`

- [ ] **Step 3: Implement** `motor_test/pca9685_motor.py`

```python
"""MotorOutput adapter for one TB6612FNG channel on the Waveshare Motor Driver HAT.

The register sequence follows Waveshare's PCA9685.py sample driver, but duty is
written as a raw 12-bit count instead of Waveshare's percentage (which scales by
40 and never reaches the full range).
"""

import math
import time
from typing import Protocol

from motor_test.ramp import PWM_MAX_COUNT, PWM_RESOLUTION

MODE1 = 0x00
PRESCALE = 0xFE
LED0_ON_L = 0x06
MODE1_SLEEP = 0x10
MODE1_RESTART = 0x80
OSCILLATOR_HZ = 25_000_000


class I2CBus(Protocol):
    """The subset of smbus2.SMBus this adapter uses."""

    def write_byte_data(self, i2c_addr: int, register: int, value: int) -> None: ...

    def read_byte_data(self, i2c_addr: int, register: int) -> int: ...


class Pca9685Motor:
    """Drives one H-bridge channel: a PWM speed pin plus two direction pins."""

    def __init__(
        self,
        bus: I2CBus,
        address: int,
        pwm_channel: int,
        in1_channel: int,
        in2_channel: int,
        pwm_freq_hz: float,
    ):
        self._bus = bus
        self._address = address
        self._pwm_channel = pwm_channel
        self._in1_channel = in1_channel
        self._in2_channel = in2_channel
        self._write(MODE1, 0x00)
        self._set_frequency(pwm_freq_hz)

    def set_forward(self) -> None:
        self._set_off_count(self._in1_channel, 0)
        self._set_off_count(self._in2_channel, PWM_MAX_COUNT)

    def set_duty(self, count: int) -> None:
        if not 0 <= count <= PWM_MAX_COUNT:
            raise ValueError(f"duty count must be between 0 and {PWM_MAX_COUNT}, got {count}")
        self._set_off_count(self._pwm_channel, count)

    def stop(self) -> None:
        self.set_duty(0)

    def _set_frequency(self, freq_hz: float) -> None:
        prescale = math.floor(OSCILLATOR_HZ / PWM_RESOLUTION / freq_hz - 1 + 0.5)
        mode = self._bus.read_byte_data(self._address, MODE1)
        self._write(MODE1, (mode & 0x7F) | MODE1_SLEEP)
        self._write(PRESCALE, prescale)
        self._write(MODE1, mode)
        time.sleep(0.005)  # same settle time Waveshare's driver waits before restart
        self._write(MODE1, mode | MODE1_RESTART)

    def _set_off_count(self, channel: int, off_count: int) -> None:
        base = LED0_ON_L + 4 * channel
        self._write(base, 0)
        self._write(base + 1, 0)
        self._write(base + 2, off_count & 0xFF)
        self._write(base + 3, off_count >> 8)

    def _write(self, register: int, value: int) -> None:
        self._bus.write_byte_data(self._address, register, value)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add motor_test/pca9685_motor.py tests/test_pca9685_motor.py
git commit -m "Add PCA9685 adapter for a Motor Driver HAT channel"
```

---

### Task 4: main.py composition root

**Files:**
- Create: `main.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `ramp_profile`, `run_ramp_loop`, `Pca9685Motor`, `smbus2.SMBus`
- Produces: `main.exit_on_sigterm(signum, frame) -> NoReturn` and `main.main() -> None`

- [ ] **Step 1: Write the failing tests** in `tests/test_main.py`

```python
import signal

import pytest

import main


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_configured_ramp_peaks_at_six_volts_over_a_two_second_cycle():
    counts = main.ramp_profile(main.PEAK_VOLTS, main.SUPPLY_VOLTS, main.STEPS_PER_CYCLE)
    assert max(counts) == 2048
    assert main.CYCLE_S / main.STEPS_PER_CYCLE == pytest.approx(0.04)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'main'`

- [ ] **Step 3: Implement** `main.py`

```python
"""Motor Driver HAT smoke test: loops motor B through a 0 -> 6 V -> 0 V ramp."""

import signal
import time

from smbus2 import SMBus

from motor_test.pca9685_motor import Pca9685Motor
from motor_test.ramp import ramp_profile
from motor_test.smoke_test import run_ramp_loop

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
        run_ramp_loop(motor, counts, CYCLE_S / STEPS_PER_CYCLE, time.sleep)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "Add main.py wiring motor B ramp loop with SIGTERM cleanup"
```

---

### Task 5: Update script

**Files:**
- Create: `deploy/jack-update.sh`, `tests/test_jack_update.py`

**Interfaces:**
- Produces: `deploy/jack-update.sh`, run as `bash /opt/jack/deploy/jack-update.sh`. The env var `JACK_REPO_DIR` (default `/opt/jack`) lets tests point it at a temp checkout. It exits 0 when up to date or updated, and non-zero on git failure. It calls `systemctl restart jack.service` only when it changed the checkout.

The tests use real git repos in `tmp_path`: a bare "origin", a "work" clone to make commits, and a "checkout" clone standing in for `/opt/jack`. They run the **checkout's own copy** of the script, as on the Pi. Only `systemctl` is replaced, with a shim on `PATH` that logs its arguments.

- [ ] **Step 1: Write the failing tests** in `tests/test_jack_update.py`

```python
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATE_SCRIPT = REPO_ROOT / "deploy" / "jack-update.sh"
IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd, *args):
    result = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@dataclass
class Deployment:
    origin: Path
    work: Path
    checkout: Path
    systemctl_log: Path
    env: dict

    def commit_and_push(self, relative_path, content):
        path = self.work / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(self.work, "add", relative_path)
        git(self.work, "commit", "-m", f"update {relative_path}")
        git(self.work, "push", "origin", "main")
        return git(self.work, "rev-parse", "HEAD")

    def run_update(self):
        return subprocess.run(
            ["bash", str(self.checkout / "deploy" / "jack-update.sh")],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def restarts(self):
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()


@pytest.fixture
def deployment(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "checkout", "-B", "main")
    (work / "deploy").mkdir()
    shutil.copy(UPDATE_SCRIPT, work / "deploy" / "jack-update.sh")
    (work / "main.py").write_text("print('v1')\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "initial")
    git(work, "push", "-u", "origin", "main")

    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text('#!/bin/sh\necho "$@" >> "$SYSTEMCTL_LOG"\n')
    systemctl.chmod(0o755)
    systemctl_log = tmp_path / "systemctl.log"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "JACK_REPO_DIR": str(checkout),
        "SYSTEMCTL_LOG": str(systemctl_log),
    }
    return Deployment(origin, work, checkout, systemctl_log, env)


def test_up_to_date_checkout_is_left_alone(deployment):
    before = git(deployment.checkout, "rev-parse", "HEAD")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == before
    assert deployment.restarts() == []


def test_new_commit_is_deployed_and_app_restarted_once(deployment):
    new_head = deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    assert (deployment.checkout / "main.py").read_text() == "print('v2')\n"
    assert deployment.restarts() == ["restart jack.service"]


def test_second_run_after_deploy_does_not_restart_again(deployment):
    deployment.commit_and_push("main.py", "print('v2')\n")
    deployment.run_update()
    deployment.run_update()
    assert deployment.restarts() == ["restart jack.service"]


def test_local_edits_on_the_pi_are_discarded(deployment):
    (deployment.checkout / "main.py").write_text("print('hand edit')\n")
    deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert (deployment.checkout / "main.py").read_text() == "print('v2')\n"


def test_force_pushed_history_is_followed(deployment):
    (deployment.work / "main.py").write_text("print('rewritten')\n")
    git(deployment.work, "commit", "--amend", "-am", "rewritten initial")
    git(deployment.work, "push", "--force", "origin", "main")
    rewritten = git(deployment.work, "rev-parse", "HEAD")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == rewritten
    assert deployment.restarts() == ["restart jack.service"]


def test_fetch_failure_leaves_checkout_and_app_untouched(deployment):
    before = git(deployment.checkout, "rev-parse", "HEAD")
    shutil.rmtree(deployment.origin)
    result = deployment.run_update()
    assert result.returncode != 0
    assert git(deployment.checkout, "rev-parse", "HEAD") == before
    assert deployment.restarts() == []


def test_commit_that_rewrites_the_update_script_completes_cleanly(deployment):
    script = (deployment.work / "deploy" / "jack-update.sh").read_text()
    shebang, rest = script.split("\n", 1)
    padded = shebang + "\n" + "# padding to shift byte offsets\n" * 200 + rest
    new_head = deployment.commit_and_push("deploy/jack-update.sh", padded)
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    assert deployment.restarts() == ["restart jack.service"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_jack_update.py -v`
Expected: every test ERRORs in the fixture with `FileNotFoundError` for `deploy/jack-update.sh`.

- [ ] **Step 3: Implement** `deploy/jack-update.sh`

```bash
#!/bin/bash
# Deploys the latest origin/main into the jack checkout and restarts the app when it changes.
# The body is wrapped in main() so bash parses the whole script before
# `git reset` can rewrite this file underneath it.
set -euo pipefail

main() {
  local repo_dir="${JACK_REPO_DIR:-/opt/jack}"
  local branch=main
  local service=jack.service

  cd "$repo_dir"
  git fetch --quiet origin "$branch"

  local current target
  current=$(git rev-parse HEAD)
  target=$(git rev-parse "origin/$branch")
  if [[ "$current" == "$target" ]]; then
    return 0
  fi

  echo "Deploying $target (was $current)"
  git reset --hard --quiet "origin/$branch"
  systemctl restart "$service"
}

main "$@"
exit
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS, with no warnings in the output.

- [ ] **Step 5: Commit**

```bash
git add deploy/jack-update.sh tests/test_jack_update.py
git commit -m "Add git polling update script with real-git tests"
```

---

### Task 6: systemd units, installer, and README

**Files:**
- Create: `deploy/jack.service`, `deploy/jack-update.service`, `deploy/jack-update.timer`, `deploy/install.sh`, `README.md`

**Interfaces:**
- Consumes: `/opt/jack/main.py` (Task 4), `/opt/jack/deploy/jack-update.sh` (Task 5)
- Produces: the unit names `jack.service`, `jack-update.service`, `jack-update.timer`

These are verified on the Pi in Task 7 with `systemd-analyze verify` and real runs. The Mac has no systemd.

- [ ] **Step 1: Create** `deploy/jack.service`

```ini
[Unit]
Description=Jack motor HAT application

[Service]
Type=simple
User=jack
Group=jack
SupplementaryGroups=i2c
WorkingDirectory=/opt/jack
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/jack/main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create** `deploy/jack-update.service`

```ini
[Unit]
Description=Deploy latest jack commit from GitHub and restart the app on change
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/jack/deploy/jack-update.sh
```

- [ ] **Step 3: Create** `deploy/jack-update.timer`

```ini
[Unit]
Description=Check GitHub for jack updates every 60 seconds

[Timer]
OnBootSec=60
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
```

- [ ] **Step 4: Create** `deploy/install.sh`

```bash
#!/bin/bash
# One-time setup of the jack app and its auto-updater on the Raspberry Pi. Run as root.
# Safe to re-run: every step skips or overwrites idempotently.
set -euo pipefail

REPO_URL=https://github.com/robbiebyrd/jack.git
REPO_DIR=/opt/jack
BRANCH=main

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3-smbus2

# 0 = enable in raspi-config's non-interactive mode
raspi-config nonint do_i2c 0

if ! id jack &>/dev/null; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin --groups i2c jack
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

install -m 0644 \
  "$REPO_DIR/deploy/jack.service" \
  "$REPO_DIR/deploy/jack-update.service" \
  "$REPO_DIR/deploy/jack-update.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jack.service jack-update.timer

echo "Installed. If /dev/i2c-1 is missing, reboot: sudo reboot"
```

- [ ] **Step 5: Syntax-check both scripts**

Run: `bash -n deploy/install.sh && bash -n deploy/jack-update.sh && echo OK`
Expected: `OK`

- [ ] **Step 6: Create** `README.md`

````markdown
# jack

Raspberry Pi + Waveshare Motor Driver HAT. `main.py` loops motor B (MB1/MB2)
through a 0 → 6 V → 0 V ramp every 2 s on a 12 V supply. See `SPEC.md`.

## Install on the Pi (once)

```bash
scp deploy/install.sh 10.10.0.54:
ssh -t 10.10.0.54 sudo bash install.sh
```

This installs git, enables I2C, creates the `jack` system user, clones to
`/opt/jack`, and enables `jack.service` and `jack-update.timer`.

## Deploys

Push to `main`. Within about 60 s the Pi runs `git reset --hard origin/main`
in `/opt/jack` and restarts `jack.service`. Edits made on the Pi are discarded.
Changes to the unit files in `deploy/` are not reinstalled automatically.
Re-run `install.sh` for those.

## Operate

```bash
journalctl -u jack -u jack-update -f   # logs
sudo systemctl stop jack               # stop the motor now
sudo systemctl disable --now jack      # keep it off across reboots
```

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```
````

- [ ] **Step 7: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add deploy/jack.service deploy/jack-update.service deploy/jack-update.timer deploy/install.sh README.md
git commit -m "Add systemd units, Pi installer, and README"
```

---

### Task 7: Deploy to the Pi and verify on hardware

**Requires Boss:** approval to merge to `main` and push, and running the `sudo` step.

- [ ] **Step 1: Ask Boss to approve the merge and push.** Once approved:

```bash
git checkout main
git merge --ff-only wip/pi-motor-deploy
git push -u origin main
```

- [ ] **Step 2: Copy the installer and have Boss run it**

```bash
scp deploy/install.sh 10.10.0.54:
```
Boss runs: `! ssh -t 10.10.0.54 sudo bash install.sh`
Expected: it ends with `Installed.` and no errors.

- [ ] **Step 3: Verify the units and I2C**

```bash
ssh 10.10.0.54 'systemd-analyze verify /etc/systemd/system/jack.service /etc/systemd/system/jack-update.service /etc/systemd/system/jack-update.timer; ls -l /dev/i2c-1; id jack; systemctl is-active jack jack-update.timer; systemctl is-enabled jack jack-update.timer'
```
Expected: no `systemd-analyze` output, `/dev/i2c-1` owned by group `i2c` with mode `crw-rw----`, `jack` in groups `jack,i2c`, both units `active` and `enabled`. If `/dev/i2c-1` is missing, Boss reboots and this step is re-run.

- [ ] **Step 4: Verify the app is running**

```bash
ssh 10.10.0.54 'journalctl -u jack -n 20 --no-pager'
```
Expected: `Looping motor B 0 -> 6.0 V -> 0 every 2.0 s on 12.0 V supply` and no tracebacks. Boss confirms with a meter on MB1/MB2 that the average voltage cycles between about 0 and about 6 V every 2 s.

- [ ] **Step 5: Verify that stopping stops the motor**

Boss runs: `! ssh -t 10.10.0.54 sudo systemctl stop jack`
Expected: `journalctl -u jack` shows a clean stop with no traceback, and the meter reads 0 V. Then `! ssh -t 10.10.0.54 sudo systemctl start jack`.

- [ ] **Step 6: Verify survival across reboot without login**

Boss runs: `! ssh -t 10.10.0.54 sudo reboot`. After it comes back (without logging in on the console):
```bash
ssh 10.10.0.54 'systemctl is-active jack jack-update.timer; journalctl -u jack -b -n 5 --no-pager'
```
Expected: both `active`, and the startup line appears for this boot.

- [ ] **Step 7: Verify the auto-update end to end** (Boss approves this push)

Change the startup `print` in `main.py` to include `[deploy check]`, run `.venv/bin/pytest`, commit, and push to `main`. Then wait until the updater has run at least once more (at most about 60 s):
```bash
ssh 10.10.0.54 'journalctl -u jack-update -n 5 --no-pager; journalctl -u jack -n 3 --no-pager; git -C /opt/jack rev-parse HEAD'
```
Expected: `jack-update` logs `Deploying <sha>`, `jack` logs the `[deploy check]` line, and the Pi's HEAD equals the local `git rev-parse HEAD`. Then revert the print change, commit, and push the same way. Boss approves that push too.
