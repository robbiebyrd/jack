# Pi Motor Smoke Test and Git-Driven Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Loop a 0 → 6 V → 0 V ramp on both motor channels (A and B) of the Waveshare Motor Driver HAT from a boot-time system service on the Pi, and redeploy automatically within about 60 s of a push to `main`.

**Architecture:** Hexagonal Python app. A pure domain module (`ramp.py`) computes duty counts, a `MotorOutput` port has a PCA9685 adapter, a small application loop (`smoke_test.py`) plays the ramp, an sd_notify adapter pings the systemd watchdog, and `main.py` wires them together. Deployment is plain systemd: a self-recovering app service (`Restart=always`, watchdog) running as the `jack` system user, plus a root timer that runs a git polling script.

**Tech Stack:** Python 3.13 (Pi: Debian 13 trixie, aarch64), smbus2 0.4.3 (apt `python3-smbus2` on the Pi), pytest (dev only, on the Mac), bash, git, systemd.

**Spec:** `SPEC.md`

## Global Constraints

- I2C bus 1, PCA9685 address `0x40`, PWM frequency 50 Hz.
- Motor A channels: PWMA = 0, AIN1 = 1, AIN2 = 2. Motor B channels: PWMB = 5, BIN1 = 3, BIN2 = 4. Forward = IN1 low, IN2 high. Both motors are driven together, forward only.
- VIN = 12 V. Peak = 6 V, which is count 2048 of 4096.
- Cycle: 2 s total (1 s up, 1 s down), 50 steps of 40 ms, repeated back-to-back with no pause.
- On SIGTERM or any exception, both motors short-brake before exit (duty 0, then IN1 = IN2 = high). A failure braking one motor must not skip the other.
- Self-recovery: `Type=notify`, `NotifyAccess=main`, `WatchdogSec=10`, `Restart=always`, `RestartSec=5`, `StartLimitIntervalSec=0`. Send `READY=1` after motor init and `WATCHDOG=1` after every completed cycle.
- Checkout lives at `/opt/jack`, owned by root, cloned from `https://github.com/robbiebyrd/jack.git`.
- App runs as the system user `jack` (no home, `/usr/sbin/nologin`, member of `i2c`), not tied to any login.
- The updater runs as root every 60 s (`OnBootSec=60`, `OnUnitActiveSec=60`). It uses `git reset --hard origin/main`, and local Pi edits are discarded.
- The Pi uses system Python and apt packages only (no pip or venv on the Pi).
- Never push or merge to `main` without Boss's explicit approval. Boss runs every `sudo` step.

## Review Focus

1. **Peak equal to supply:** a 4096 count would set the PCA9685 "full off" bit and turn the motor *off*. Counts must cap at 4095. Covered in Task 1 and Task 6.
2. **SIGTERM during a sleep** (`systemctl stop` or a deploy restart): both motors must short-brake before the process exits, even if braking one fails. Covered in Task 2, Task 6 and Task 7.
3. **`git fetch` failure** (network down, GitHub unreachable): the updater exits non-zero, leaves the checkout alone, and does not restart the app. Covered in Task 8.
4. **Remote history rewritten** (force-push) or a tracked file edited by hand on the Pi: the Pi still converges exactly to `origin/main`. Covered in Task 8.
5. **A commit that changes `jack-update.sh` itself** while the running script is being rewritten by `git reset`: the run completes normally and restarts once. Covered in Task 8.

---

## File Structure

| Path | Responsibility |
|---|---|
| `motor_test/__init__.py` | Package marker (empty) |
| `motor_test/ramp.py` | Domain: triangle ramp to 12-bit duty counts |
| `motor_test/ports.py` | Port: `MotorOutput` protocol |
| `motor_test/smoke_test.py` | Application: play a ramp cycle forever, always stop the motor |
| `motor_test/pca9685_motor.py` | Adapter (Task 3). Replaced in Task 6 by `pca9685.py` + `tb6612_motor.py` |
| `motor_test/pca9685.py` | Adapter: the PCA9685 chip, shared by both motors |
| `motor_test/tb6612_motor.py` | Adapter: one TB6612FNG channel (A or B) with forward, backward, reverse flag, short brake |
| `motor_test/motor_group.py` | Application: drive several motors as one `MotorOutput`, brake all on stop |
| `motor_test/systemd_notify.py` | Adapter: send sd_notify messages (READY, WATCHDOG) to systemd |
| `main.py` | Composition root: constants, SIGTERM handler, wiring |
| `deploy/jack-update.sh` | Poll origin/main, reset, and restart the app on change |
| `deploy/jack.service` | App system unit |
| `deploy/jack-update.service` | Oneshot unit that runs the update script |
| `deploy/jack-update.timer` | Runs the updater every 60 s |
| `deploy/install.sh` | One-time root setup on the Pi |
| `tests/test_ramp.py`, `tests/test_smoke_test.py`, `tests/test_pca9685_motor.py`, `tests/test_systemd_notify.py`, `tests/test_main.py`, `tests/test_jack_update.py`, `tests/test_units.py` | Tests |
| `pyproject.toml` | pytest config |
| `requirements-dev.txt` | Dev-only pins (pytest, smbus2) |
| `README.md` | Install, logs, how to stop |

---

### Task 1: Dev tooling and ramp domain

**Files:**
- Create: `pyproject.toml`, `requirements-dev.txt`, `motor_test/__init__.py`, `motor_test/ramp.py`, `tests/test_ramp.py`
- Modify: `.gitignore`

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

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml requirements-dev.txt motor_test/__init__.py motor_test/ramp.py tests/test_ramp.py
git commit -m "Add triangle ramp domain with pytest tooling"
```

---

### Task 2: MotorOutput port and ramp loop

**Files:**
- Create: `motor_test/ports.py`, `motor_test/smoke_test.py`, `tests/test_smoke_test.py`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime. Counts are passed in.
- Produces: `motor_test.ports.MotorOutput` (Protocol with `set_forward() -> None`, `set_duty(count: int) -> None`, `stop() -> None`) and `motor_test.smoke_test.run_ramp_loop(motor: MotorOutput, counts: Sequence[int], step_s: float, sleep: Callable[[float], None], on_cycle: Callable[[], None]) -> None`, which never returns normally. `on_cycle` is called once after each completed cycle (main.py uses it for the watchdog ping).

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


def no_op():
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
        run_ramp_loop(motor, [0, 10], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[0] == ("forward",)
    assert motor.calls[1] == ("duty", 0)


def test_plays_counts_in_order_and_repeats_the_cycle():
    motor = RecordingMotor()
    counts = [0, 5, 9, 5]
    sleep, _ = sleep_that_raises_after(len(counts) * 2)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, counts, step_s=0.04, sleep=sleep, on_cycle=no_op)
    duties = [call[1] for call in motor.calls if call[0] == "duty"]
    assert duties == counts * 2


def test_waits_one_step_after_each_count():
    motor = RecordingMotor()
    sleep, slept = sleep_that_raises_after(6)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert slept == [0.04] * 6


def test_stops_motor_when_loop_is_interrupted_by_an_error():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(3)
    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[-1] == ("stop",)


def test_stops_motor_on_system_exit_from_sigterm():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(2, exception=SystemExit(0))
    with pytest.raises(SystemExit):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=no_op)
    assert motor.calls[-1] == ("stop",)


def test_on_cycle_runs_once_after_each_completed_cycle():
    motor = RecordingMotor()
    events = []

    def sleep(seconds):
        events.append("step")
        if len(events) >= 7:  # 2 full cycles of 3 steps, plus 1 on_cycle marker per cycle
            raise LoopEnded

    with pytest.raises(LoopEnded):
        run_ramp_loop(motor, [0, 5, 9], step_s=0.04, sleep=sleep, on_cycle=lambda: events.append("cycle"))
    assert events == ["step", "step", "step", "cycle", "step", "step", "step"]


def test_empty_cycle_is_rejected_without_touching_the_motor():
    motor = RecordingMotor()
    sleep, _ = sleep_that_raises_after(1)
    with pytest.raises(ValueError):
        run_ramp_loop(motor, [], step_s=0.04, sleep=sleep, on_cycle=no_op)
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
    on_cycle: Callable[[], None],
) -> None:
    """Drive `motor` forward through `counts` cycle after cycle, forever.

    `on_cycle` runs after every completed cycle, which proves the loop is alive.

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
            on_cycle()
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

### Task 4: systemd notify adapter

**Files:**
- Create: `motor_test/systemd_notify.py`, `tests/test_systemd_notify.py`

**Interfaces:**
- Produces: `motor_test.systemd_notify.notify(message: str) -> None`, which reads `NOTIFY_SOCKET` from the environment on each call and does nothing if it is unset. Also `socket_address(path: str) -> str`, which maps a leading `@` to `\0` (Linux abstract namespace).

The sd_notify protocol: send the message as one datagram on an `AF_UNIX` `SOCK_DGRAM` socket to the path in `$NOTIFY_SOCKET`. The tests use a real socket in a short temp dir, because macOS limits Unix socket paths to about 104 bytes and pytest's `tmp_path` can exceed that.

- [ ] **Step 1: Write the failing tests** in `tests/test_systemd_notify.py`

```python
import os
import shutil
import socket
import tempfile

import pytest

from motor_test.systemd_notify import notify, socket_address


@pytest.fixture
def notify_socket(monkeypatch):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(1)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield server
    server.close()
    shutil.rmtree(directory)


def test_notify_sends_message_as_one_datagram(notify_socket):
    notify("READY=1")
    assert notify_socket.recv(64) == b"READY=1"


def test_notify_sends_each_watchdog_ping(notify_socket):
    notify("WATCHDOG=1")
    notify("WATCHDOG=1")
    assert notify_socket.recv(64) == b"WATCHDOG=1"
    assert notify_socket.recv(64) == b"WATCHDOG=1"


def test_notify_does_nothing_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    notify("READY=1")  # must not raise


def test_at_prefix_maps_to_abstract_namespace():
    assert socket_address("@/org/freedesktop/systemd1/notify") == "\0/org/freedesktop/systemd1/notify"


def test_filesystem_path_is_used_as_is():
    assert socket_address("/run/systemd/notify") == "/run/systemd/notify"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_systemd_notify.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'motor_test.systemd_notify'`

- [ ] **Step 3: Implement** `motor_test/systemd_notify.py`

```python
"""Minimal sd_notify client so systemd knows the app is ready and still alive."""

import os
import socket


def socket_address(path: str) -> str:
    """Translate systemd's `@name` notation into a Linux abstract socket address."""
    if path.startswith("@"):
        return "\0" + path[1:]
    return path


def notify(message: str) -> None:
    """Send one sd_notify message. Does nothing when not started by systemd."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), socket_address(path))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add motor_test/systemd_notify.py tests/test_systemd_notify.py
git commit -m "Add sd_notify adapter for systemd readiness and watchdog pings"
```

---

### Task 5: main.py composition root

**Files:**
- Create: `main.py`, `tests/test_main.py`

**Interfaces:**
- Consumes: `ramp_profile`, `run_ramp_loop`, `Pca9685Motor`, `notify`, `smbus2.SMBus`
- Produces: `main.exit_on_sigterm(signum, frame) -> NoReturn`, `main.ping_watchdog() -> None`, and `main.main() -> None`

- [ ] **Step 1: Write the failing tests** in `tests/test_main.py`

```python
import os
import shutil
import signal
import socket
import tempfile

import pytest

import main


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(monkeypatch):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "notify.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as server:
        server.bind(path)
        server.settimeout(1)
        monkeypatch.setenv("NOTIFY_SOCKET", path)
        main.ping_watchdog()
        assert server.recv(64) == b"WATCHDOG=1"
    shutil.rmtree(directory)


def test_cycle_is_well_inside_the_watchdog_timeout():
    watchdog_s = 10  # WatchdogSec in deploy/jack.service
    assert main.CYCLE_S * 2 < watchdog_s


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_main.py
git commit -m "Add main.py wiring motor B ramp loop with SIGTERM cleanup and watchdog pings"
```

---

### Task 6: Split the PCA9685 chip and TB6612 channel adapters

This replaces `motor_test/pca9685_motor.py` (Task 3). The HAT has two motor channels on one PCA9685, so the chip is initialized once and shared. Short brake, the reverse flag and backward are borrowed from https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT). Only the ideas are borrowed, because that library drives Pi GPIO, not a PCA9685.

**Files:**
- Create: `motor_test/pca9685.py`, `motor_test/tb6612_motor.py`, `tests/fakes.py`, `tests/test_pca9685.py`, `tests/test_tb6612_motor.py`
- Delete: `motor_test/pca9685_motor.py`, `tests/test_pca9685_motor.py`
- Modify: `tests/test_smoke_test.py` (import `RecordingMotor` from `tests/fakes.py` instead of defining it)
- Do NOT modify `main.py` in this task. Task 7 rewires it. Until then `main.py` still imports the deleted module, so `tests/test_main.py` will fail at collection. Run the other test files as listed below.

**Interfaces:**
- Consumes: `motor_test.ramp.PWM_RESOLUTION`, `motor_test.ramp.PWM_MAX_COUNT`, `motor_test.ports.MotorOutput`
- Produces:
  - `motor_test.pca9685.Pca9685(bus: I2CBus, address: int, pwm_freq_hz: float)` with `set_off_count(channel: int, off_count: int) -> None`, which raises `ValueError` outside 0..4095
  - `motor_test.pca9685.I2CBus` (Protocol)
  - `motor_test.tb6612_motor.MotorChannels(pwm: int, in1: int, in2: int)` (NamedTuple), `MOTOR_A = MotorChannels(pwm=0, in1=1, in2=2)`, `MOTOR_B = MotorChannels(pwm=5, in1=3, in2=4)`
  - `motor_test.tb6612_motor.Tb6612Motor(chip: Pca9685, channels: MotorChannels, reverse: bool = False)` with `set_forward()`, `set_backward()`, `set_duty(count: int)`, and `stop()` (short brake). It satisfies `MotorOutput`.
  - `tests/fakes.py` with `RecordingBus` and `RecordingMotor`, imported as `from tests.fakes import ...`

Register map: channel `n` = `0x06 + 4n` (ON_L, ON_H, OFF_L, OFF_H). Motor A: PWMA 0 (0x06–0x09), AIN1 1 (0x0A–0x0D), AIN2 2 (0x0E–0x11). Motor B: BIN1 3 (0x12–0x15), BIN2 4 (0x16–0x19), PWMB 5 (0x1A–0x1D).

- [ ] **Step 1: Create the shared test fakes** in `tests/fakes.py`

```python
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
```

In `tests/test_smoke_test.py`, delete the `RecordingMotor` class and add `from tests.fakes import RecordingMotor` after the existing `from motor_test.smoke_test import run_ramp_loop` line.

Run: `.venv/bin/pytest tests/test_smoke_test.py -v`
Expected: all PASS, since only the import location moved.

- [ ] **Step 2: Write the failing chip tests** in `tests/test_pca9685.py`

```python
import pytest

from motor_test.pca9685 import Pca9685
from tests.fakes import RecordingBus

ADDRESS = 0x40


def make_chip():
    bus = RecordingBus()
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    return bus, chip


def test_init_resets_mode_and_sets_50hz_prescale_like_waveshare():
    bus, _ = make_chip()
    assert bus.writes == [
        (ADDRESS, 0x00, 0x00),  # MODE1 reset
        (ADDRESS, 0x00, 0x10),  # sleep to change prescale
        (ADDRESS, 0xFE, 121),  # prescale for 50 Hz
        (ADDRESS, 0x00, 0x00),  # wake
        (ADDRESS, 0x00, 0x80),  # restart
    ]


def test_set_off_count_writes_channel_registers():
    bus, chip = make_chip()
    bus.writes.clear()
    chip.set_off_count(5, 2048)
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x08),
    ]


def test_full_count_splits_across_off_low_and_high_registers():
    bus, chip = make_chip()
    bus.writes.clear()
    chip.set_off_count(0, 4095)
    assert bus.writes == [
        (ADDRESS, 0x06, 0x00),
        (ADDRESS, 0x07, 0x00),
        (ADDRESS, 0x08, 0xFF),
        (ADDRESS, 0x09, 0x0F),
    ]


@pytest.mark.parametrize("count", [-1, 4096])
def test_out_of_range_count_is_rejected_without_writing(count):
    bus, chip = make_chip()
    bus.writes.clear()
    with pytest.raises(ValueError):
        chip.set_off_count(5, count)
    assert bus.writes == []
```

- [ ] **Step 3: Write the failing motor tests** in `tests/test_tb6612_motor.py`

```python
import pytest

from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, MotorChannels, Tb6612Motor
from tests.fakes import RecordingBus

ADDRESS = 0x40


def make_motor(channels, reverse=False):
    bus = RecordingBus()
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    bus.writes.clear()
    return bus, chip, Tb6612Motor(chip, channels, reverse=reverse)


def off_count(bus, channel):
    """Decode the 12-bit OFF count the chip holds for `channel`."""
    base = 0x06 + 4 * channel
    return bus.registers[base + 2] | (bus.registers[base + 3] << 8)


def test_channel_mapping_matches_waveshare_sample_code():
    assert MOTOR_A == MotorChannels(pwm=0, in1=1, in2=2)
    assert MOTOR_B == MotorChannels(pwm=5, in1=3, in2=4)


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_forward_drives_in1_low_and_in2_high(channels):
    bus, _, motor = make_motor(channels)
    motor.set_forward()
    assert off_count(bus, channels.in1) == 0
    assert off_count(bus, channels.in2) == PWM_MAX_COUNT


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_backward_drives_in1_high_and_in2_low(channels):
    bus, _, motor = make_motor(channels)
    motor.set_backward()
    assert off_count(bus, channels.in1) == PWM_MAX_COUNT
    assert off_count(bus, channels.in2) == 0


def test_reverse_flag_swaps_forward_and_backward():
    bus, _, motor = make_motor(MOTOR_B, reverse=True)
    motor.set_forward()
    assert (off_count(bus, 3), off_count(bus, 4)) == (PWM_MAX_COUNT, 0)
    motor.set_backward()
    assert (off_count(bus, 3), off_count(bus, 4)) == (0, PWM_MAX_COUNT)


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_set_duty_drives_the_motors_pwm_channel(channels):
    bus, _, motor = make_motor(channels)
    motor.set_duty(2048)
    assert off_count(bus, channels.pwm) == 2048


def test_two_motors_on_one_chip_keep_separate_channels():
    bus, chip, motor_a = make_motor(MOTOR_A)
    motor_b = Tb6612Motor(chip, MOTOR_B)
    motor_a.set_duty(2048)
    motor_b.set_duty(1000)
    assert off_count(bus, 0) == 2048
    assert off_count(bus, 5) == 1000


def test_stop_zeroes_duty_before_short_braking():
    bus, _, motor = make_motor(MOTOR_B)
    motor.set_forward()
    motor.set_duty(2048)
    bus.writes.clear()
    motor.stop()
    assert bus.writes[:4] == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x00),
    ]
    assert off_count(bus, 5) == 0
    assert off_count(bus, 3) == PWM_MAX_COUNT
    assert off_count(bus, 4) == PWM_MAX_COUNT


def test_out_of_range_duty_is_rejected():
    bus, _, motor = make_motor(MOTOR_A)
    with pytest.raises(ValueError):
        motor.set_duty(4096)
    assert bus.writes == []
```

- [ ] **Step 4: Run the new tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pca9685.py tests/test_tb6612_motor.py -v`
Expected: collection errors `ModuleNotFoundError: No module named 'motor_test.pca9685'` and `No module named 'motor_test.tb6612_motor'`

- [ ] **Step 5: Implement** `motor_test/pca9685.py`

```python
"""PCA9685 12-bit PWM controller on the Waveshare Motor Driver HAT.

The register sequence follows Waveshare's PCA9685.py sample driver, but outputs
are written as raw 12-bit counts instead of Waveshare's percentage (which scales
by 40 and never reaches the full range).
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


class Pca9685:
    """One PCA9685 chip. Create it once and share it between the motors it drives."""

    def __init__(self, bus: I2CBus, address: int, pwm_freq_hz: float):
        self._bus = bus
        self._address = address
        self._write(MODE1, 0x00)
        self._set_frequency(pwm_freq_hz)

    def set_off_count(self, channel: int, off_count: int) -> None:
        """Hold `channel` high for `off_count` of every 4096 ticks (0 = always low)."""
        if not 0 <= off_count <= PWM_MAX_COUNT:
            raise ValueError(f"off count must be between 0 and {PWM_MAX_COUNT}, got {off_count}")
        base = LED0_ON_L + 4 * channel
        self._write(base, 0)
        self._write(base + 1, 0)
        self._write(base + 2, off_count & 0xFF)
        self._write(base + 3, off_count >> 8)

    def _set_frequency(self, freq_hz: float) -> None:
        prescale = math.floor(OSCILLATOR_HZ / PWM_RESOLUTION / freq_hz - 1 + 0.5)
        mode = self._bus.read_byte_data(self._address, MODE1)
        self._write(MODE1, (mode & 0x7F) | MODE1_SLEEP)
        self._write(PRESCALE, prescale)
        self._write(MODE1, mode)
        time.sleep(0.005)  # same settle time Waveshare's driver waits before restart
        self._write(MODE1, mode | MODE1_RESTART)

    def _write(self, register: int, value: int) -> None:
        self._bus.write_byte_data(self._address, register, value)
```

- [ ] **Step 6: Implement** `motor_test/tb6612_motor.py`

```python
"""MotorOutput adapter for one TB6612FNG H-bridge channel driven through the PCA9685.

Short brake, backward, and the reverse-polarity flag are borrowed from
https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT).
"""

from typing import NamedTuple

from motor_test.pca9685 import Pca9685
from motor_test.ramp import PWM_MAX_COUNT

LOW = 0
HIGH = PWM_MAX_COUNT


class MotorChannels(NamedTuple):
    """PCA9685 channels wired to one TB6612FNG channel's PWM, IN1 and IN2 pins."""

    pwm: int
    in1: int
    in2: int


# Channel mapping from Waveshare's Motor Driver HAT sample code.
MOTOR_A = MotorChannels(pwm=0, in1=1, in2=2)
MOTOR_B = MotorChannels(pwm=5, in1=3, in2=4)


class Tb6612Motor:
    """One DC motor on the HAT. `reverse=True` flips direction for a motor wired with swapped leads."""

    def __init__(self, chip: Pca9685, channels: MotorChannels, reverse: bool = False):
        self._chip = chip
        self._channels = channels
        self._reverse = reverse

    def set_forward(self) -> None:
        self._set_direction(forward=not self._reverse)

    def set_backward(self) -> None:
        self._set_direction(forward=self._reverse)

    def set_duty(self, count: int) -> None:
        self._chip.set_off_count(self._channels.pwm, count)

    def stop(self) -> None:
        """Short brake: zero the duty, then drive IN1 and IN2 high so the TB6612FNG shorts the motor leads."""
        self.set_duty(0)
        self._set_inputs(HIGH, HIGH)

    def _set_direction(self, forward: bool) -> None:
        # Waveshare's "forward" is IN1 low, IN2 high.
        if forward:
            self._set_inputs(LOW, HIGH)
        else:
            self._set_inputs(HIGH, LOW)

    def _set_inputs(self, in1: int, in2: int) -> None:
        self._chip.set_off_count(self._channels.in1, in1)
        self._chip.set_off_count(self._channels.in2, in2)
```

- [ ] **Step 7: Delete the replaced adapter and its tests**

```bash
git rm motor_test/pca9685_motor.py tests/test_pca9685_motor.py
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ramp.py tests/test_smoke_test.py tests/test_systemd_notify.py tests/test_pca9685.py tests/test_tb6612_motor.py -v`
Expected: all PASS, with no warnings. `tests/test_main.py` is excluded because `main.py` still imports the deleted module until Task 7.

- [ ] **Step 9: Commit**

```bash
git add tests/fakes.py tests/test_smoke_test.py tests/test_pca9685.py tests/test_tb6612_motor.py motor_test/pca9685.py motor_test/tb6612_motor.py
git commit -m "Split PCA9685 chip and TB6612 channel adapters; add short brake, reverse, both channels"
```

---

### Task 7: Motor group and two-channel main.py

**Files:**
- Create: `motor_test/motor_group.py`, `tests/test_motor_group.py`
- Modify: `main.py` (full replacement below), `tests/test_main.py` (one new test)

**Interfaces:**
- Consumes: `Pca9685`, `Tb6612Motor`, `MOTOR_A`, `MOTOR_B` (Task 6); `RecordingMotor` from `tests/fakes.py` (Task 6); `run_ramp_loop`, `ramp_profile`, `notify` (earlier tasks)
- Produces: `motor_test.motor_group.MotorGroup(motors: Sequence[MotorOutput])`, which is itself a `MotorOutput`

- [ ] **Step 1: Write the failing tests** in `tests/test_motor_group.py`

```python
import pytest

from motor_test.motor_group import MotorGroup
from tests.fakes import RecordingMotor


class MotorThatFailsToStop(RecordingMotor):
    def stop(self):
        super().stop()
        raise OSError("I2C write failed")


def test_commands_reach_every_motor_in_order():
    motor_a, motor_b = RecordingMotor(), RecordingMotor()
    group = MotorGroup([motor_a, motor_b])
    group.set_forward()
    group.set_duty(2048)
    group.stop()
    expected = [("forward",), ("duty", 2048), ("stop",)]
    assert motor_a.calls == expected
    assert motor_b.calls == expected


def test_stop_still_brakes_later_motors_when_one_fails():
    failing, healthy = MotorThatFailsToStop(), RecordingMotor()
    group = MotorGroup([failing, healthy])
    with pytest.raises(OSError, match="I2C write failed"):
        group.stop()
    assert healthy.calls == [("stop",)]


def test_empty_group_is_rejected():
    with pytest.raises(ValueError):
        MotorGroup([])
```

Add this test to the end of `tests/test_main.py`:

```python
def test_both_hat_channels_are_driven():
    assert main.DRIVEN_CHANNELS == (main.MOTOR_A, main.MOTOR_B)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_motor_group.py tests/test_main.py -v`
Expected: collection errors: `No module named 'motor_test.motor_group'`, and for `test_main.py`, `No module named 'motor_test.pca9685_motor'` (removed in Task 6).

- [ ] **Step 3: Implement** `motor_test/motor_group.py`

```python
"""Drives several motors as one MotorOutput."""

from collections.abc import Sequence

from motor_test.ports import MotorOutput


class MotorGroup:
    """Sends every command to each motor, in order."""

    def __init__(self, motors: Sequence[MotorOutput]):
        if not motors:
            raise ValueError("MotorGroup needs at least one motor")
        self._motors = list(motors)

    def set_forward(self) -> None:
        for motor in self._motors:
            motor.set_forward()

    def set_duty(self, count: int) -> None:
        for motor in self._motors:
            motor.set_duty(count)

    def stop(self) -> None:
        """Stop every motor, even if stopping one fails, then re-raise the first failure."""
        failures = []
        for motor in self._motors:
            try:
                motor.stop()
            except Exception as failure:
                failures.append(failure)
        if failures:
            raise failures[0]
```

- [ ] **Step 4: Replace** `main.py` entirely with:

```python
"""Motor Driver HAT smoke test: loops motors A and B through a 0 -> 6 V -> 0 V ramp."""

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
DRIVEN_CHANNELS = (MOTOR_A, MOTOR_B)

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


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    counts = ramp_profile(PEAK_VOLTS, SUPPLY_VOLTS, STEPS_PER_CYCLE)
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        motors = MotorGroup([Tb6612Motor(chip, channels) for channels in DRIVEN_CHANNELS])
        print(f"Looping motors A and B 0 -> {PEAK_VOLTS} V -> 0 every {CYCLE_S} s on {SUPPLY_VOLTS} V supply")
        notify("READY=1")
        run_ramp_loop(motors, counts, CYCLE_S / STEPS_PER_CYCLE, time.sleep, ping_watchdog)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the full suite to verify it passes**

Run: `.venv/bin/pytest -v`
Expected: all PASS, with no warnings.

- [ ] **Step 6: Commit**

```bash
git add motor_test/motor_group.py tests/test_motor_group.py main.py tests/test_main.py
git commit -m "Drive both HAT motor channels through a MotorGroup that brakes all on exit"
```

---

### Task 8: Update script

**Files:**
- Create: `deploy/jack-update.sh`, `tests/test_jack_update.py`

**Interfaces:**
- Produces: `deploy/jack-update.sh`, run as `bash /opt/jack/deploy/jack-update.sh`. The env var `JACK_REPO_DIR` (default `/opt/jack`) lets tests point it at a temp checkout. It exits 0 when up to date or updated, and non-zero on git failure. It calls `systemctl try-restart jack.service` only when it changed the checkout.

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
    assert deployment.restarts() == ["try-restart jack.service"]


def test_second_run_after_deploy_does_not_restart_again(deployment):
    deployment.commit_and_push("main.py", "print('v2')\n")
    deployment.run_update()
    deployment.run_update()
    assert deployment.restarts() == ["try-restart jack.service"]


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
    assert deployment.restarts() == ["try-restart jack.service"]


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
    assert deployment.restarts() == ["try-restart jack.service"]
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
  systemctl try-restart "$service"
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

### Task 9: systemd units, installer, and README

**Files:**
- Create: `deploy/jack.service`, `deploy/jack-update.service`, `deploy/jack-update.timer`, `deploy/install.sh`, `README.md`, `tests/test_units.py`

**Interfaces:**
- Consumes: `/opt/jack/main.py` (Task 7), `/opt/jack/deploy/jack-update.sh` (Task 8)
- Produces: the unit names `jack.service`, `jack-update.service`, `jack-update.timer`

These are verified on the Pi in Task 10 with `systemd-analyze verify` and real failure drills. The Mac has no systemd, so `tests/test_units.py` pins the self-recovery settings in the unit file.

- [ ] **Step 1: Write the failing test** `tests/test_units.py`

```python
import configparser
from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "jack.service"


def load_unit():
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str  # systemd keys are case-sensitive
    parser.read(UNIT)
    return parser


def test_app_restarts_after_any_exit_and_never_gives_up():
    unit = load_unit()
    assert unit["Service"]["Restart"] == "always"
    assert unit["Service"]["RestartSec"] == "5"
    assert unit["Unit"]["StartLimitIntervalSec"] == "0"


def test_app_is_supervised_by_the_systemd_watchdog():
    unit = load_unit()
    assert unit["Service"]["Type"] == "notify"
    assert unit["Service"]["NotifyAccess"] == "main"
    assert unit["Service"]["WatchdogSec"] == "10"


def test_app_runs_as_jack_at_boot_without_login():
    unit = load_unit()
    assert unit["Service"]["User"] == "jack"
    assert unit["Service"]["SupplementaryGroups"] == "i2c"
    assert unit["Install"]["WantedBy"] == "multi-user.target"
```

Run: `.venv/bin/pytest tests/test_units.py -v`
Expected: FAIL. `configparser` reads the missing file as empty, so the tests fail with `KeyError: 'Service'` or `KeyError: 'Unit'`.

- [ ] **Step 2: Create** `deploy/jack.service`

```ini
[Unit]
Description=Jack motor HAT application
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
WatchdogSec=10
User=jack
Group=jack
SupplementaryGroups=i2c
WorkingDirectory=/opt/jack
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 /opt/jack/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create** `deploy/jack-update.service`

```ini
[Unit]
Description=Deploy latest jack commit from GitHub and restart the app on change
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/jack/deploy/jack-update.sh
```

- [ ] **Step 4: Create** `deploy/jack-update.timer`

```ini
[Unit]
Description=Check GitHub for jack updates every 60 seconds

[Timer]
OnBootSec=60
OnUnitActiveSec=60

[Install]
WantedBy=timers.target
```

- [ ] **Step 5: Create** `deploy/install.sh`

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

- [ ] **Step 6: Syntax-check both scripts**

Run: `bash -n deploy/install.sh && bash -n deploy/jack-update.sh && echo OK`
Expected: `OK`

- [ ] **Step 7: Create** `README.md`

````markdown
# jack

Raspberry Pi + Waveshare Motor Driver HAT. `main.py` loops both motor channels
(MA1/MA2 and MB1/MB2) through a 0 → 6 V → 0 V ramp every 2 s on a 12 V supply,
and short-brakes them on exit. See `SPEC.md`.

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

## Self-recovery

`jack.service` restarts after any exit (`Restart=always`, 5 s delay, never
gives up) and is watched by the systemd watchdog. If the loop stops pinging
for 10 s, systemd kills and restarts it.

## Operate

```bash
journalctl -u jack -u jack-update -f   # logs
sudo systemctl stop jack               # stop (brake) the motors now
sudo systemctl disable --now jack      # keep it off across reboots
```

## Develop

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```
````

- [ ] **Step 8: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all PASS, including `tests/test_units.py`.

- [ ] **Step 9: Commit**

```bash
git add tests/test_units.py deploy/jack.service deploy/jack-update.service deploy/jack-update.timer deploy/install.sh README.md
git commit -m "Add systemd units, Pi installer, and README"
```

---

### Task 10: Deploy to the Pi and verify on hardware

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
Expected: `Looping motors A and B 0 -> 6.0 V -> 0 every 2.0 s on 12.0 V supply` and no tracebacks. Boss confirms with a meter on MA1/MA2 and on MB1/MB2 that the average voltage cycles between about 0 and about 6 V every 2 s.

- [ ] **Step 5: Verify that stopping stops the motor**

Boss runs: `! ssh -t 10.10.0.54 sudo systemctl stop jack`
Expected: `journalctl -u jack` shows a clean stop with no traceback, and the meter reads 0 V across both MA1/MA2 and MB1/MB2 (short brake). Then `! ssh -t 10.10.0.54 sudo systemctl start jack`.

- [ ] **Step 6: Crash drill (restart after being killed)**

Boss runs: `! ssh -t 10.10.0.54 'sudo kill -9 $(systemctl show -p MainPID --value jack)'`
Wait 10 s, then:
```bash
ssh 10.10.0.54 'systemctl is-active jack; systemctl show -p NRestarts jack; journalctl -u jack -n 10 --no-pager'
```
Expected: `active`, `NRestarts` went up by 1, and the journal shows the kill followed by a new startup line.

- [ ] **Step 7: Hang drill (watchdog fires)**

Boss runs: `! ssh -t 10.10.0.54 'sudo kill -STOP $(systemctl show -p MainPID --value jack)'`
Wait 20 s, then:
```bash
ssh 10.10.0.54 'systemctl is-active jack; systemctl show -p NRestarts jack; journalctl -u jack -n 15 --no-pager'
```
Expected: the journal shows `Watchdog timeout` for `jack.service`, followed by a restart and a new startup line. `NRestarts` went up by 1, and the service is `active`.

- [ ] **Step 8: Verify survival across reboot without login**

Boss runs: `! ssh -t 10.10.0.54 sudo reboot`. After it comes back (without logging in on the console):
```bash
ssh 10.10.0.54 'systemctl is-active jack jack-update.timer; journalctl -u jack -b -n 5 --no-pager'
```
Expected: both `active`, and the startup line appears for this boot.

- [ ] **Step 9: Stopped stays stopped**

Boss runs: `! ssh -t 10.10.0.54 sudo systemctl stop jack`. Then push a trivial commit (Boss approves this push) and wait for `jack-update` to log `Deploying`:
```bash
ssh 10.10.0.54 'journalctl -u jack-update -n 5 --no-pager'
```
Then check that the stop was not overridden:
```bash
ssh 10.10.0.54 'systemctl is-active jack'
```
Expected: `inactive`, and the meter reads 0 V across both MA1/MA2 and MB1/MB2. Then: `! ssh -t 10.10.0.54 sudo systemctl start jack`.

- [ ] **Step 10: Updater survives a failed fetch**

First resolve GitHub's address on the Pi:
```bash
ssh 10.10.0.54 'getent ahostsv4 github.com'
```
Expected: an address inside GitHub's published range (see https://api.github.com/meta); on 2026-09-28 it resolved to 140.82.114.3, inside `140.82.112.0/20`. Then block that range temporarily:
```bash
ssh -t 10.10.0.54 'sudo ip route add blackhole 140.82.112.0/20'
```
Wait for at least one timer tick, then check the failure was logged and the timer kept firing:
```bash
ssh 10.10.0.54 'journalctl -u jack-update -n 10 --no-pager; systemctl list-timers jack-update.timer'
```
Expected: a failed run in the journal, and `jack-update.timer` still scheduled to fire again. Then remove the route and confirm the next run succeeds:
```bash
ssh -t 10.10.0.54 'sudo ip route del blackhole 140.82.112.0/20'
```
```bash
ssh 10.10.0.54 'journalctl -u jack-update -n 5 --no-pager'
```
Expected: the next run completes without the earlier failure.

- [ ] **Step 11: Verify the auto-update end to end** (Boss approves this push)

Change the startup `print` in `main.py` to include `[deploy check]`, run `.venv/bin/pytest`, commit, and push to `main`. Then wait until the updater has run at least once more (at most about 60 s):
```bash
ssh 10.10.0.54 'journalctl -u jack-update -n 5 --no-pager; journalctl -u jack -n 3 --no-pager; git -C /opt/jack rev-parse HEAD'
```
Expected: `jack-update` logs `Deploying <sha>`, `jack` logs the `[deploy check]` line, and the Pi's HEAD equals the local `git rev-parse HEAD`. Then revert the print change, commit, and push the same way. Boss approves that push too.
