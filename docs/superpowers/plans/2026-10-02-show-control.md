# Show Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an external show controller drive Jack's four motors (mouth, hand, arm pivot, elbow on two Motor Driver HATs) over OSC and HTTP, with named poses, a dead-man safety model, and a live/show switch for the mouth.

**Architecture:** One process, one owner of the hardware. HTTP and OSC servers run as daemon threads that only write a thread-safe command board. The existing 20 ms talk loop, paced by the sound card, reads the board each tick, turns each motor's target into a drive through a pure per-motor driver (slew, max hold, rest pulse, brake/coast), and writes a motor only when its drive changes. `poses.toml` owns every motor's voltages, including the mouth's for lip sync.

**Tech Stack:** Python 3.13 (Pi, Debian trixie), pytest on the Mac, `tomllib`, `http.server.ThreadingHTTPServer`, `python-osc` 1.10.2 (pip), smbus2, systemd.

**Spec:** `SPEC.md`, section "Show control" (and the note under "Mouth control" about voltages moving to `poses.toml`). Read both before any task.

## Global Constraints

- Work directly on `main`; no branches or worktrees. Other sessions/agents may commit in this checkout: stage only your task's files; commit with `git commit --only -m "<msg>" -m "Co-Authored-By: Claude <noreply@anthropic.com>" -- <paths>`; never `git add -A`/`.`/`commit -a`; never revert changes you didn't make.
- Never push. Pushing `main` deploys to the Pi (10.10.0.54) within about 60 s; Boss decides when.
- Tests: `.venv/bin/pytest` from the repo root; output pristine (no warnings, no stray prints).
- TDD for every code change: failing test first, see it fail for the right reason, minimal code, see it pass.
- Supply is 12 V. Volts are signed; negative runs a motor "backward". Volts beyond ±12 V are rejected.
- Motors (spec table): `mouth` HAT `0x40` channel B, `hand` `0x40` A, `pivot` `0x41` A (two-sided, −1…1), `elbow` `0x41` B. HAT 2 answers at `0x41` (its "A4" pad acts as A0).
- One 20 ms tick = `motor_test.pcm.TICK_S`. `max_hold_s` and non-zero `rest_pulse_s` must be whole ticks.
- `python-osc==1.10.2`, imported only in `motor_test/osc_server.py`.
- Default ports: OSC UDP 9000 (`JACK_OSC_PORT`), HTTP TCP 8080 (`JACK_HTTP_PORT`); dead-man `JACK_CONTROL_TIMEOUT_S` default 0.5; mouth mode `JACK_MOUTH_MODE` `live` (default) or `show`.
- No authentication (LAN trusted). Secrets never in the repo.
- Style: module docstring on every module, type hints, short functions, comments only for what/why; match surrounding code.

## Review Focus

1. A held slider or a show controller streaming the same value at 20–60 Hz → no I2C write per tick for an unchanged drive, and no request log spam. Test in Task 8 (one drive call for a steady value) and Task 10 (`log_message` silenced).
2. Malformed OSC (no argument, a string where a float belongs, a bool, NaN, an unknown address) → ignored with a rate-limited log line; the OSC thread keeps serving. Tests in Task 7 (`handle_osc`) and Task 9 (bad packet then good packet).
3. A mouth command while the mouth is in `live` mode → HTTP `409`, OSC ignored + logged; lip sync is never fought. Tests in Task 7 and Task 10.
4. A typo or an over-supply voltage in `/etc/jack/poses.toml` → the app refuses to start with a one-line message naming the file/motor/key. Tests in Task 3.
5. OSC and HTTP commanding the same motor at once → last command wins, no torn state; the loop's read is consistent. Test in Task 6 (concurrent writers and reader).

---

### Task 1: Board check on the Pi (one-off, with Boss; nothing committed)

Prove HAT 2 (`0x41`) drives both channels before any wiring. Jack keeps running on `0x40`; this touches only `0x41`. Nothing is connected to HAT 2's terminals, so full 12 V-scaled duty is safe.

- [ ] **Step 1: Write the check script on the Pi**

```bash
ssh -o BatchMode=yes 10.10.0.54 'cat > /tmp/hat2_check.py' <<'EOF'
"""Throwaway: drive HAT 2 (0x41) channel A then B, both directions, while Boss reads a meter."""
import sys
sys.path.insert(0, "/opt/jack")
from smbus2 import SMBus
from motor_test.pca9685 import Pca9685
from motor_test.ramp import volts_to_count
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor

with SMBus(1) as bus:
    chip = Pca9685(bus, 0x41, 50)
    for label, channels, terminals in (("A", MOTOR_A, "MA1/MA2"), ("B", MOTOR_B, "MB1/MB2")):
        motor = Tb6612Motor(chip, channels)
        try:
            for volts in (6.0, -6.0):
                motor.drive(volts_to_count(volts, 12.0))
                input(f"Channel {label}: {volts:+} V commanded. Read {terminals}, then press Enter… ")
        finally:
            motor.stop()
    print("Done; both channels braked.")
EOF
```

- [ ] **Step 2: Boss runs it and reads the meter**

Boss: `ssh -t 10.10.0.54 python3 /tmp/hat2_check.py`. Expected: about +6 V then about −6 V across MA1/MA2, then the same across MB1/MB2 (PWM average of 12 V × duty, minus a small driver drop). Record the four readings.

- [ ] **Step 3: Record and clean up**

Append the readings to SPEC.md "Hardware facts" (second-HAT bullet) as "Board check 2026-10-02: A +x/−y V, B +x/−y V". `ssh 10.10.0.54 rm /tmp/hat2_check.py`. Commit SPEC.md: `git commit --only -m "Spec: record the second HAT's board check" ... -- SPEC.md`.

If a channel reads ~0 V or the script raises `OSError`, stop and investigate with Boss before Task 11 (the app will require both boards).

---

### Task 2: Motor registry and coasting

**Files:**
- Create: `motor_test/motors.py`, `tests/test_motors.py`
- Modify: `motor_test/tb6612_motor.py`, `motor_test/ports.py`, `tests/fakes.py`, `tests/test_tb6612_motor.py`

**Interfaces:**
- Produces: `motor_test.motors`: `MotorSpec(name: str, address: int, channel: str, two_sided: bool)`, `HAT1_ADDRESS = 0x40`, `HAT2_ADDRESS = 0x41`, `MOTORS: tuple[MotorSpec, ...]`, `MOTOR_NAMES: tuple[str, ...]` (`("mouth", "hand", "pivot", "elbow")`), `motor_spec(name) -> MotorSpec` (ValueError for unknown).
- Produces: `motor_test.tb6612_motor.MOTOR_CHANNELS: dict[str, MotorChannels]` (`{"A": MOTOR_A, "B": MOTOR_B}`), `Tb6612Motor.coast()`.
- Produces: `MotorOutput.coast()` in the port; `tests.fakes.RecordingMotor.coast()` records `("coast",)`.

- [ ] **Step 1: Failing registry tests** — `tests/test_motors.py`:

```python
import pytest

from motor_test.motors import HAT1_ADDRESS, HAT2_ADDRESS, MOTOR_NAMES, MOTORS, MotorSpec, motor_spec


def test_the_four_motors_and_where_they_are_wired():
    assert MOTORS == (
        MotorSpec("mouth", 0x40, "B", two_sided=False),
        MotorSpec("hand", 0x40, "A", two_sided=False),
        MotorSpec("pivot", 0x41, "A", two_sided=True),
        MotorSpec("elbow", 0x41, "B", two_sided=False),
    )
    assert (HAT1_ADDRESS, HAT2_ADDRESS) == (0x40, 0x41)


def test_motor_names_in_registry_order():
    assert MOTOR_NAMES == ("mouth", "hand", "pivot", "elbow")


def test_motor_spec_looks_up_by_name():
    assert motor_spec("elbow").channel == "B"


def test_unknown_motor_is_rejected_listing_the_known_ones():
    with pytest.raises(ValueError, match="mouth, hand, pivot, elbow"):
        motor_spec("tail")
```

Run `.venv/bin/pytest tests/test_motors.py -v` → `ModuleNotFoundError`.

- [ ] **Step 2: Implement `motor_test/motors.py`**

```python
"""Jack's four motors by name: which HAT and channel each is wired to."""

from dataclasses import dataclass

HAT1_ADDRESS = 0x40
# The second HAT's bridged pad behaves as A0, so it answers at 0x41 (SPEC.md "Hardware facts").
HAT2_ADDRESS = 0x41


@dataclass(frozen=True)
class MotorSpec:
    name: str
    address: int
    channel: str
    # True when commands run −1…1 (pivot: left…right) instead of 0…1.
    two_sided: bool


MOTORS: tuple[MotorSpec, ...] = (
    MotorSpec("mouth", HAT1_ADDRESS, "B", two_sided=False),
    MotorSpec("hand", HAT1_ADDRESS, "A", two_sided=False),
    MotorSpec("pivot", HAT2_ADDRESS, "A", two_sided=True),
    MotorSpec("elbow", HAT2_ADDRESS, "B", two_sided=False),
)
MOTOR_NAMES: tuple[str, ...] = tuple(spec.name for spec in MOTORS)


def motor_spec(name: str) -> MotorSpec:
    for spec in MOTORS:
        if spec.name == name:
            return spec
    raise ValueError(f"unknown motor {name!r}; expected one of {', '.join(MOTOR_NAMES)}")
```

Run → 4 passed.

- [ ] **Step 3: Failing coast tests** — append to `tests/test_tb6612_motor.py` (it already has `make_motor`, `off_count`, `PwmWriteFailsBus`; add `MOTOR_CHANNELS` to its `from motor_test.tb6612_motor import …` line):

```python
def test_channels_by_letter():
    assert MOTOR_CHANNELS == {"A": MOTOR_A, "B": MOTOR_B}


@pytest.mark.parametrize("channels", [MOTOR_A, MOTOR_B])
def test_coast_zeroes_duty_and_drives_both_inputs_low(channels):
    bus, _, motor = make_motor(channels)
    motor.drive(2000)
    motor.coast()
    assert off_count(bus, channels.pwm) == 0
    assert (off_count(bus, channels.in1), off_count(bus, channels.in2)) == (0, 0)


def test_drive_after_coast_restores_the_direction_pins():
    bus, _, motor = make_motor(MOTOR_A)
    motor.coast()
    motor.drive(500)
    assert (off_count(bus, 1), off_count(bus, 2)) == (0, PWM_MAX_COUNT)
    assert off_count(bus, 0) == 500


def test_coast_still_releases_the_inputs_when_zeroing_duty_fails():
    bus = PwmWriteFailsBus(failing_channel=0)
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    motor = Tb6612Motor(chip, MOTOR_A)
    with pytest.raises(OSError):
        motor.coast()
    assert (off_count(bus, 1), off_count(bus, 2)) == (0, 0)
```

Run → fail (`ImportError` / `AttributeError: coast`).

- [ ] **Step 4: Implement coasting** in `motor_test/tb6612_motor.py`:

After `MOTOR_B = …` add:

```python
MOTOR_CHANNELS: dict[str, MotorChannels] = {"A": MOTOR_A, "B": MOTOR_B}
```

Replace `stop` with `stop` + `coast` sharing one helper:

```python
    def stop(self) -> None:
        """Short brake: zero the duty, then drive IN1 and IN2 high so the TB6612FNG shorts the motor leads."""
        self._release(HIGH)

    def coast(self) -> None:
        """Coast: zero the duty, then drive IN1 and IN2 low so the TB6612FNG leaves the motor leads open."""
        self._release(LOW)

    def _release(self, level: int) -> None:
        """Zero the duty and set both inputs to `level`.

        Each write is attempted even if an earlier one raises, so a transient I2C
        error zeroing the duty doesn't skip the input pins.
        """
        self._forward = None
        attempt_all(
            [
                lambda: self._set_duty(0),
                lambda: self._chip.set_off_count(self._channels.in1, level),
                lambda: self._chip.set_off_count(self._channels.in2, level),
            ]
        )
```

In `motor_test/ports.py`, add to `MotorOutput` after `stop`:

```python
    def coast(self) -> None:
        """Stop driving and leave the motor free to turn (no braking)."""
        ...
```

In `tests/fakes.py`, add to `RecordingMotor`:

```python
    def coast(self):
        self.calls.append(("coast",))
```

- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_motors.py tests/test_tb6612_motor.py -v && .venv/bin/pytest -q` → all pass (existing stop tests prove the refactor kept braking).

- [ ] **Step 6: Commit** `motor_test/motors.py motor_test/tb6612_motor.py motor_test/ports.py tests/fakes.py tests/test_motors.py tests/test_tb6612_motor.py` — message: "Name Jack's four motors across both HATs, and let a motor coast"

---

### Task 3: Motor profiles from `poses.toml`

**Files:**
- Create: `motor_test/poses.py`, `poses.toml`, `tests/profiles.py`, `tests/test_poses.py`

**Interfaces:**
- Consumes: `MOTOR_NAMES`, `motor_spec` (Task 2); `TICK_S`; `whole_steps` (ramp.py).
- Produces: `motor_test.poses`: `Pose(volts: float, seconds: float)`, `MotorProfile(calibrated, min_v, max_v, sign, slew_v_per_s, max_hold_s, rest, rest_pulse_v, rest_pulse_s, poses: Mapping[str, Pose])` with `.volts_for(value: float) -> float`; `REST_MODES = ("brake", "coast")`; `load_profiles(paths: Sequence[Path], supply_volts: float) -> dict[str, MotorProfile]`.
- Produces: `tests.profiles`: `MOUTH`, `HAND`, `PIVOT`, `ELBOW`, `PROFILES`, `profile(name, **overrides)`.

- [ ] **Step 1: Test profiles helper** — `tests/profiles.py`:

```python
"""MotorProfiles written out for tests, so tests don't depend on poses.toml's tunable values."""

import dataclasses

from motor_test.poses import MotorProfile, Pose

MOUTH = MotorProfile(
    calibrated=True, min_v=1.0, max_v=6.0, sign=-1, slew_v_per_s=48.0, max_hold_s=1.0,
    rest="brake", rest_pulse_v=0.5, rest_pulse_s=0.08,
    poses={"close": Pose(1.0, 0.25), "relax": Pose(-2.0, 0.5), "open": Pose(-6.0, 0.5)},
)
HAND = MotorProfile(
    calibrated=False, min_v=1.0, max_v=2.0, sign=1, slew_v_per_s=24.0, max_hold_s=1.0,
    rest="brake", rest_pulse_v=0.0, rest_pulse_s=0.0, poses={"curl": Pose(2.0, 0.5)},
)
PIVOT = dataclasses.replace(HAND, poses={"left": Pose(-2.0, 0.5), "right": Pose(2.0, 0.5)})
ELBOW = dataclasses.replace(HAND, poses={"up": Pose(2.0, 0.5)})
PROFILES = {"mouth": MOUTH, "hand": HAND, "pivot": PIVOT, "elbow": ELBOW}


def profile(name, **overrides):
    """PROFILES[name] with some fields replaced."""
    return dataclasses.replace(PROFILES[name], **overrides)
```

(It imports `motor_test.poses`, so it fails until Step 4 — expected.)

- [ ] **Step 2: Failing loader tests** — `tests/test_poses.py`:

```python
from pathlib import Path

import pytest

from motor_test.poses import Pose, load_profiles
from tests.profiles import HAND, MOUTH, PIVOT, profile

SUPPLY = 12.0
REPO_POSES = Path(__file__).resolve().parent.parent / "poses.toml"

MOTOR_TABLE = """
[{name}]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1
slew_v_per_s = 24.0
max_hold_s = 1.0
rest = "brake"
[{name}.poses]
go = {{ volts = 2.0, seconds = 0.5 }}
"""


def write(tmp_path, text, name="poses.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


def all_motors(**extra_by_motor):
    """A valid file for all four motors; extra_by_motor[name] is appended to that motor's table."""
    return "".join(
        MOTOR_TABLE.format(name=name).replace("[" + name + ".poses]", extra_by_motor.get(name, "") + "\n[" + name + ".poses]")
        for name in ("mouth", "hand", "pivot", "elbow")
    )


def test_repo_poses_file_matches_the_spec():
    profiles = load_profiles([REPO_POSES], SUPPLY)
    mouth = profiles["mouth"]
    assert (mouth.calibrated, mouth.min_v, mouth.max_v, mouth.sign) == (True, 1.0, 6.0, -1)
    assert (mouth.slew_v_per_s, mouth.rest, mouth.rest_pulse_v, mouth.rest_pulse_s) == (48.0, "brake", 0.5, 0.08)
    assert mouth.poses == {"close": Pose(1.0, 0.25), "relax": Pose(-2.0, 0.5), "open": Pose(-6.0, 0.5)}
    for name, poses in (("hand", {"curl"}), ("pivot", {"left", "right"}), ("elbow", {"up"})):
        placeholder = profiles[name]
        assert placeholder.calibrated is False
        assert (placeholder.max_v, placeholder.max_hold_s, placeholder.slew_v_per_s, placeholder.sign) == (2.0, 1.0, 24.0, 1)
        assert set(placeholder.poses) == poses
        assert all(abs(pose.volts) == 2.0 and pose.seconds == 0.5 for pose in placeholder.poses.values())


def test_value_maps_from_min_to_max_in_the_motors_direction():
    assert MOUTH.volts_for(0.0) == 0.0
    assert MOUTH.volts_for(1.0) == -6.0
    assert MOUTH.volts_for(0.5) == -3.5
    assert HAND.volts_for(0.25) == 1.25


def test_two_sided_value_runs_the_other_way_below_zero():
    assert PIVOT.volts_for(-1.0) == -2.0
    assert PIVOT.volts_for(1.0) == 2.0


def test_override_file_replaces_fields_and_poses_one_by_one(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, '[hand]\nmax_v = 3.0\n[hand.poses]\ncurl = { volts = 3.0, seconds = 1.0 }\n', "pi.toml")
    hand = load_profiles([base, override], SUPPLY)["hand"]
    assert hand.max_v == 3.0
    assert hand.min_v == 1.0
    assert hand.poses == {"go": Pose(2.0, 0.5), "curl": Pose(3.0, 1.0)}


def test_missing_override_file_is_skipped(tmp_path):
    base = write(tmp_path, all_motors())
    assert load_profiles([base, tmp_path / "absent.toml"], SUPPLY)["elbow"].max_v == 2.0


def test_missing_first_file_is_an_error(tmp_path):
    with pytest.raises(OSError):
        load_profiles([tmp_path / "absent.toml"], SUPPLY)


@pytest.mark.parametrize(
    "text, message",
    [
        ("[tail]\nmin_v = 1.0\n", "unknown motor"),
        ("[hand]\nmax_volts = 3.0\n", "max_volts"),
        ("[hand]\nmax_v = 13.0\n", "supply"),
        ("[hand]\nmin_v = 3.0\n", "min_v"),
        ("[hand]\nsign = 2\n", "sign"),
        ("[hand]\nrest = \"float\"\n", "rest"),
        ("[hand]\nmax_hold_s = 0.33\n", "max_hold_s"),
        ("[hand]\nrest_pulse_v = 0.5\nrest_pulse_s = 0.05\n", "rest_pulse_s"),
        ("[hand]\nslew_v_per_s = 0\n", "slew_v_per_s"),
        ("[hand]\nmax_v = nan\n", "max_v"),
        ("[hand.poses]\ncurl = { volts = 20.0, seconds = 0.5 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0, seconds = 0 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0 }\n", "curl"),
        ("hand = 3\n", "hand"),
        ("[hand\n", "pi.toml"),
    ],
)
def test_invalid_entries_are_rejected_naming_what_is_wrong(tmp_path, text, message):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, text, "pi.toml")
    with pytest.raises(ValueError, match=message):
        load_profiles([base, override], SUPPLY)


def test_a_motor_missing_from_every_file_is_an_error(tmp_path):
    only_mouth = write(tmp_path, MOTOR_TABLE.format(name="mouth"))
    with pytest.raises(ValueError, match="hand, pivot, elbow"):
        load_profiles([only_mouth], SUPPLY)


def test_profile_helper_replaces_fields():
    assert profile("mouth", slew_v_per_s=1000.0).slew_v_per_s == 1000.0
```

Run `.venv/bin/pytest tests/test_poses.py -v` → `ModuleNotFoundError: motor_test.poses`.

- [ ] **Step 3: Write `poses.toml`** (repo root):

```toml
# Each motor's drive limits and named poses (SPEC.md "Show control").
# Override any entry on the Pi in /etc/jack/poses.toml (same layout, only the keys you change),
# then: sudo systemctl restart jack
# Volts are signed: negative runs the motor "backward". min_v..max_v is the magnitude a
# command value of just-above-0 .. 1.0 drives, in the direction `sign` (pivot: sign of "right").

[mouth]
calibrated = true
min_v = 1.0          # smallest opening (lip-sync tuning, 2026-09-28)
max_v = 6.0          # fully open (calibration)
sign = -1            # negative volts open the mouth
slew_v_per_s = 48.0  # twice the calibrated 24 V/s ramp, Boss's call (2026-09-28)
max_hold_s = 1.0     # guess: fully open stalls against its end stop
rest = "brake"
rest_pulse_v = 0.5   # the mouth holds its pose unpowered, so resting needs a close pulse
rest_pulse_s = 0.08

[mouth.poses]
close = { volts = 1.0, seconds = 0.25 }
relax = { volts = -2.0, seconds = 0.5 }
open = { volts = -6.0, seconds = 0.5 }

# UNCALIBRATED placeholders until measured with: calibrate.py hand
[hand]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1             # unverified: which sign curls the hand
slew_v_per_s = 24.0
max_hold_s = 1.0
rest = "brake"

[hand.poses]
curl = { volts = 2.0, seconds = 0.5 }

# UNCALIBRATED placeholders until measured with: calibrate.py pivot
[pivot]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1             # unverified: which sign swings right
slew_v_per_s = 24.0
max_hold_s = 1.0
rest = "brake"

[pivot.poses]
left = { volts = -2.0, seconds = 0.5 }
right = { volts = 2.0, seconds = 0.5 }

# UNCALIBRATED placeholders until measured with: calibrate.py elbow
[elbow]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1             # unverified: which sign raises the elbow
slew_v_per_s = 24.0
max_hold_s = 1.0
rest = "brake"

[elbow.poses]
up = { volts = 2.0, seconds = 0.5 }
```

- [ ] **Step 4: Implement `motor_test/poses.py`**

```python
"""Each motor's drive limits and named poses, from poses.toml plus an optional override file.

The repo's poses.toml holds the defaults; /etc/jack/poses.toml on the Pi may override any key or
pose. Every motor is validated at load, so a bad file stops the app before anything moves.
See "Poses and per-motor settings" in SPEC.md.
"""

import math
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from motor_test.motors import MOTOR_NAMES
from motor_test.pcm import TICK_S
from motor_test.ramp import whole_steps

REST_MODES = ("brake", "coast")
_REQUIRED = ("calibrated", "min_v", "max_v", "sign", "slew_v_per_s", "max_hold_s", "rest")
_OPTIONAL = ("rest_pulse_v", "rest_pulse_s", "poses")


@dataclass(frozen=True)
class Pose:
    volts: float
    seconds: float


@dataclass(frozen=True)
class MotorProfile:
    calibrated: bool
    min_v: float
    max_v: float
    sign: int
    slew_v_per_s: float
    max_hold_s: float
    rest: str
    rest_pulse_v: float
    rest_pulse_s: float
    poses: Mapping[str, Pose]

    def volts_for(self, value: float) -> float:
        """Signed volts for a command value: 0 is rest; otherwise min_v..max_v by |value|, in `sign`'s direction."""
        if value == 0:
            return 0.0
        magnitude = self.min_v + (self.max_v - self.min_v) * min(1.0, abs(value))
        return math.copysign(magnitude, value) * self.sign


def load_profiles(paths: Sequence[Path], supply_volts: float) -> dict[str, MotorProfile]:
    """Merge the files in order (later keys and poses replace earlier ones) and validate every motor.

    The first file must exist; later ones are skipped when absent. Raises ValueError naming the
    file, motor and key for anything invalid.
    """
    merged: dict[str, dict] = {}
    for index, path in enumerate(paths):
        if index > 0 and not path.exists():
            continue
        for name, table in _read(path).items():
            if name not in MOTOR_NAMES:
                raise ValueError(f"{path}: unknown motor [{name}]; expected one of {', '.join(MOTOR_NAMES)}")
            if not isinstance(table, dict):
                raise ValueError(f"{path}: {name} must be a [{name}] table")
            _merge(merged.setdefault(name, {}), table)
    missing = [name for name in MOTOR_NAMES if name not in merged]
    if missing:
        raise ValueError(f"no settings for {', '.join(missing)} in {', '.join(str(p) for p in paths)}")
    return {name: _profile(name, merged[name], supply_volts) for name in MOTOR_NAMES}


def _read(path: Path) -> dict:
    with path.open("rb") as file:
        try:
            return tomllib.load(file)
        except tomllib.TOMLDecodeError as error:
            raise ValueError(f"{path}: {error}") from error


def _merge(into: dict, table: dict) -> None:
    for key, value in table.items():
        if key == "poses" and isinstance(value, dict):
            into.setdefault("poses", {}).update(value)
        else:
            into[key] = value


def _profile(name: str, table: dict, supply_volts: float) -> MotorProfile:
    where = f"[{name}]"
    unknown = set(table) - set(_REQUIRED) - set(_OPTIONAL)
    if unknown:
        raise ValueError(f"{where}: unknown key(s) {', '.join(sorted(unknown))}")
    missing = [key for key in _REQUIRED if key not in table]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")
    if not isinstance(table["calibrated"], bool):
        raise ValueError(f"{where}: calibrated must be true or false")
    min_v = _number(where, table, "min_v")
    max_v = _number(where, table, "max_v")
    if not 0 < min_v <= max_v:
        raise ValueError(f"{where}: need 0 < min_v <= max_v, got min_v {min_v}, max_v {max_v}")
    _within_supply(where, "max_v", max_v, supply_volts)
    if table["sign"] not in (1, -1) or isinstance(table["sign"], bool):
        raise ValueError(f"{where}: sign must be 1 or -1, got {table['sign']!r}")
    slew = _number(where, table, "slew_v_per_s")
    if slew <= 0:
        raise ValueError(f"{where}: slew_v_per_s must be positive, got {slew}")
    max_hold_s = _ticks(where, "max_hold_s", _number(where, table, "max_hold_s"))
    if table["rest"] not in REST_MODES:
        raise ValueError(f"{where}: rest must be one of {', '.join(REST_MODES)}, got {table['rest']!r}")
    rest_pulse_v = _number(where, table, "rest_pulse_v", default=0.0)
    _within_supply(where, "rest_pulse_v", rest_pulse_v, supply_volts)
    rest_pulse_s = _number(where, table, "rest_pulse_s", default=0.0)
    if rest_pulse_s < 0:
        raise ValueError(f"{where}: rest_pulse_s must not be negative, got {rest_pulse_s}")
    if rest_pulse_s > 0:
        _ticks(where, "rest_pulse_s", rest_pulse_s)
    poses = {pose: _pose(f"{where} pose {pose}", spec, supply_volts) for pose, spec in table.get("poses", {}).items()}
    return MotorProfile(
        calibrated=table["calibrated"], min_v=min_v, max_v=max_v, sign=table["sign"], slew_v_per_s=slew,
        max_hold_s=max_hold_s, rest=table["rest"], rest_pulse_v=rest_pulse_v, rest_pulse_s=rest_pulse_s,
        poses=poses,
    )


def _pose(where: str, spec: object, supply_volts: float) -> Pose:
    if not isinstance(spec, dict) or set(spec) != {"volts", "seconds"}:
        raise ValueError(f"{where}: must be {{ volts = <V>, seconds = <s> }}, got {spec!r}")
    volts = _number(where, spec, "volts")
    _within_supply(where, "volts", volts, supply_volts)
    seconds = _number(where, spec, "seconds")
    if seconds <= 0:
        raise ValueError(f"{where}: seconds must be positive, got {seconds}")
    return Pose(volts, seconds)


def _number(where: str, table: dict, key: str, default: float | None = None) -> float:
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"{where}: {key} must be a finite number, got {value!r}")
    return float(value)


def _within_supply(where: str, key: str, volts: float, supply_volts: float) -> None:
    if abs(volts) > supply_volts:
        raise ValueError(f"{where}: {key} {volts} V exceeds the {supply_volts} V supply")


def _ticks(where: str, key: str, seconds: float) -> float:
    try:
        whole_steps(seconds, TICK_S)
    except ValueError as error:
        raise ValueError(f"{where}: {key}: {error}") from error
    return seconds
```

- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_poses.py -v && .venv/bin/pytest -q` → all pass. If a parametrized case fails only because its message doesn't contain the expected word, fix the message in `poses.py` (not the test) so it names what's wrong.

- [ ] **Step 6: Commit** `motor_test/poses.py poses.toml tests/profiles.py tests/test_poses.py` — "Load every motor's drive limits and poses from poses.toml"

---

### Task 4: Lip sync reads the mouth's voltages from its profile

**Files:**
- Modify: `motor_test/talk_settings.py`, `motor_test/lip_sync.py`, `motor_test/talk_loop.py`, `main.py`, `lipsync_wav.py`
- Test: `tests/test_talk_settings.py`, `tests/test_lip_sync.py`, `tests/test_talk_loop.py`, `tests/test_main.py`, `tests/test_lipsync_wav.py`

**Interfaces:**
- Consumes: `MotorProfile`, `load_profiles` (Task 3); `tests.profiles`.
- Produces: `TalkSettings` without `open_min_v`, `open_max_v`, `open_slew_v_per_s`, `close_v`, `close_s` (and without `close_ticks`); `MouthController(settings: TalkSettings, mouth: MotorProfile)`; interim `run_talk_loop(sources, sink, mouth, idle_motors, settings, mouth_profile, supply_volts, on_second, until=…)` (Task 8 replaces this signature); `check_supply` removed; `main.POSES_PATHS: tuple[Path, Path]` (repo `poses.toml`, `/etc/jack/poses.toml`); `main.motor_profiles(paths=POSES_PATHS) -> dict[str, MotorProfile]` (SystemExit with message on error); `main.MOVED_TO_POSES: dict[str, str]`.

- [ ] **Step 1: Update tests first.**

`tests/test_talk_settings.py`: `test_starting_values_match_the_spec` asserts only the remaining fields — `open_curve 2.0`, `stall_v 5.0`, `max_stall_s 0.5`, `attack_s 0.01`, `release_s 0.04`, gates `-22/-27/-14`, `mouth_lead_ms 0.0`, `max_backlog_ms 200.0` — and add `assert not hasattr(settings, "open_min_v")`. `test_durations_convert_to_whole_20_ms_ticks`: drop the `close_ticks` assertion. Remove from the impossible-settings parametrization every case naming a moved field (`open_min_v`, `open_max_v`, `open_slew_v_per_s`, `close_v`, `close_s`); keep the rest. Those rules are now covered by `tests/test_poses.py`.

`tests/test_lip_sync.py`: replace its imports/helpers top with:

```python
import pytest

from motor_test.lip_sync import MouthController
from motor_test.pcm import TICK_S
from motor_test.talk_settings import TalkSettings
from tests.profiles import MOUTH, profile

_DEFAULTS = TalkSettings()
GATE = _DEFAULTS.gate_open_db
BETWEEN_GATES = (_DEFAULTS.gate_close_db + _DEFAULTS.gate_open_db) / 2
BELOW_CLOSE = _DEFAULTS.gate_close_db - 5
QUIET = _DEFAULTS.gate_close_db - 20
FULL = _DEFAULTS.full_db
HALF_LOUD = (_DEFAULTS.gate_open_db + _DEFAULTS.full_db) / 2
CLOSE_TICKS = round(MOUTH.rest_pulse_s / TICK_S)


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(**overrides), profile("mouth", slew_v_per_s=1000.0))
```

Then in its tests: every `TalkSettings(open_slew_v_per_s=1000.0, …)` becomes `TalkSettings(…)` with `profile("mouth", slew_v_per_s=1000.0)` as the controller's second argument; every `MouthController(TalkSettings(…))` passes `MOUTH` (default slew) as the second argument; `settings.close_ticks` becomes `CLOSE_TICKS` (4); the close pulse value stays `0.5`. Expected volts are unchanged (gate −1.0, half-loud −2.25 with curve 2 / −3.5 with `open_curve=1.0`, full −6.0, slew −0.96/−1.92/−2.88, stall cap −1.0). Add:

```python
def test_volts_and_close_pulse_come_from_the_mouth_profile():
    controller = MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, min_v=2.0, max_v=4.0, rest_pulse_v=0.7))
    assert controller.update(GATE) == -2.0
    assert controller.update(FULL) == -4.0
    assert controller.update(BELOW_CLOSE) == 0.7


def test_a_mouth_without_a_rest_pulse_closes_straight_to_zero():
    controller = MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, rest_pulse_v=0.0, rest_pulse_s=0.0))
    assert [controller.update(level) for level in (GATE, BELOW_CLOSE, BELOW_CLOSE)] == [-1.0, 0.0, 0.0]
```

`tests/test_talk_loop.py`: `FAST = TalkSettings(open_slew_v_per_s=1000.0)` becomes `FAST = TalkSettings()` plus `FAST_MOUTH = profile("mouth", slew_v_per_s=1000.0)` (import `from tests.profiles import profile`); `talk()` passes `FAST_MOUTH` as the new `mouth_profile` argument after `settings`; the mouth-lead test uses `TalkSettings(mouth_lead_ms=40.0)`; delete `test_voltages_beyond_the_supply_are_rejected_before_the_mouth_moves` (supply limits are validated by `load_profiles` now).

`tests/test_main.py`: in `test_non_finite_override_exits_naming_the_variable` change the parameters to `("JACK_ATTACK_S", "nan")` and `("JACK_RELEASE_S", "inf")`; delete `test_override_beyond_the_supply_exits_saying_the_settings_are_invalid`; check the other override tests use only remaining fields (switch any moved field to `gate_open_db`/`open_curve`). Add:

```python
@pytest.mark.parametrize(
    "var, key",
    [
        ("JACK_OPEN_MIN_V", "min_v"),
        ("JACK_OPEN_MAX_V", "max_v"),
        ("JACK_OPEN_SLEW_V_PER_S", "slew_v_per_s"),
        ("JACK_CLOSE_V", "rest_pulse_v"),
        ("JACK_CLOSE_S", "rest_pulse_s"),
    ],
)
def test_overrides_that_moved_to_poses_toml_stop_the_app_saying_where(var, key):
    with pytest.raises(SystemExit) as exit_info:
        main.talk_settings({var: "2"})
    message = str(exit_info.value.code)
    assert var in message and key in message and "/etc/jack/poses.toml" in message


def test_motor_profiles_load_the_repo_poses_file():
    assert main.motor_profiles()["mouth"].max_v == 6.0


def test_invalid_poses_file_exits_with_one_line(tmp_path):
    bad = tmp_path / "poses.toml"
    bad.write_text("[hand]\nmax_v = 99\n")
    with pytest.raises(SystemExit) as exit_info:
        main.motor_profiles((main.POSES_PATHS[0], bad))
    assert "99" in str(exit_info.value.code)
```

`tests/test_lipsync_wav.py`: delete `test_override_above_the_supply_is_rejected_before_touching_hardware` (the flag no longer exists). Add:

```python
def test_invalid_poses_file_is_rejected_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    bad = tmp_path / "poses.toml"
    bad.write_text("[mouth]\nmax_v = 99\n")
    monkeypatch.setattr(lipsync_wav, "POSES_PATHS", (lipsync_wav.POSES_PATHS[0], bad))
    assert lipsync_wav.main(["voice.wav"]) == 2
    assert "99" in capsys.readouterr().err
```

Run the five test files → failures for the expected reasons (unexpected keyword / missing attribute / old signature).

- [ ] **Step 2: `motor_test/talk_settings.py`** — delete the five fields and the `close_ticks` property; the positive-check tuple becomes `("stall_v", "attack_s", "release_s")`; delete the `open_max_v < open_min_v` check and the `self.close_ticks` line in `__post_init__`. Docstring: replace "except the voltages (Boss's calibration), the gates…" with "The mouth's voltages live in poses.toml (its MotorProfile); these are lip-sync behaviour only."

- [ ] **Step 3: `motor_test/lip_sync.py`** — constructor and the three uses:

```python
from motor_test.pcm import TICK_S
from motor_test.poses import MotorProfile
from motor_test.ramp import whole_steps
from motor_test.talk_settings import TalkSettings
```

```python
    def __init__(self, settings: TalkSettings, mouth: MotorProfile):
        self._settings = settings
        self._mouth = mouth
        self._slew_per_tick = mouth.slew_v_per_s * TICK_S
        self._close_ticks = whole_steps(mouth.rest_pulse_s, TICK_S) if mouth.rest_pulse_s > 0 else 0
        …(state fields unchanged)
```

- `update`: `return self._mouth.sign * self._open_magnitude(level_db)` for the open state (the mouth's sign is −1, so open stays negative).
- `_open_magnitude`: `s.open_min_v` → `self._mouth.min_v`, `s.open_max_v` → `self._mouth.max_v`.
- `_guard_stall`: cap with `self._mouth.min_v`.
- `_start_closing`: if `self._close_ticks == 0`, set state CLOSED (no pulse); else set the counter from `self._close_ticks`.
- `_closing_volts`: return `self._mouth.rest_pulse_v`.

- [ ] **Step 4: `motor_test/talk_loop.py`** — add parameter `mouth_profile: MotorProfile` after `settings`; build `MouthController(settings, mouth_profile)`; delete the `check_supply(...)` call and the `check_supply` function; import `MotorProfile` from `motor_test.poses`.

- [ ] **Step 5: `main.py`** — add `from pathlib import Path` and `from motor_test.poses import MotorProfile, load_profiles`; remove the `check_supply` import. Add after `SETTING_ENV_PREFIX`:

```python
# Repo defaults, then the Pi's override file (SPEC.md "Poses and per-motor settings").
POSES_PATHS = (Path(__file__).resolve().parent / "poses.toml", Path("/etc/jack/poses.toml"))
# Mouth settings that moved from jack.env to the mouth's entry in poses.toml: env field → poses.toml key.
MOVED_TO_POSES = {
    "open_min_v": "min_v",
    "open_max_v": "max_v",
    "open_slew_v_per_s": "slew_v_per_s",
    "close_v": "rest_pulse_v",
    "close_s": "rest_pulse_s",
}
```

At the start of `talk_settings`:

```python
    for field, key in MOVED_TO_POSES.items():
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise SystemExit(
                f"{var} moved to the mouth's entry in /etc/jack/poses.toml as {key}; "
                "remove it from /etc/jack/jack.env, then: sudo systemctl restart jack"
            )
```

In `talk_settings`, the `try` builds only `TalkSettings(**overrides)` (no supply check). Add:

```python
def motor_profiles(paths: tuple[Path, ...] = POSES_PATHS) -> dict[str, MotorProfile]:
    """Every motor's profile from poses.toml, or a one-line exit naming the problem."""
    try:
        return load_profiles(paths, SUPPLY_VOLTS)
    except (ValueError, OSError) as error:
        raise SystemExit(f"Motor settings are invalid: {error}") from error
```

In `main()`: `profiles = motor_profiles()` right after `settings = talk_settings()`, and pass `profiles["mouth"]` to `run_talk_loop` after `settings`.

- [ ] **Step 6: `lipsync_wav.py`** — import `POSES_PATHS` from `main` and `load_profiles` from `motor_test.poses`; remove the `check_supply` import. In `main`'s validation `try`: replace `check_supply(settings, SUPPLY_VOLTS)` with `mouth = load_profiles(POSES_PATHS, SUPPLY_VOLTS)["mouth"]`; pass `mouth` to `run_talk_loop` after `settings`. Update its docstring's "Settings are in…" line to also name `poses.toml` (mouth voltages).

- [ ] **Step 7: Run** the five test files, then `.venv/bin/pytest -q` → all pass; `.venv/bin/python -c "import main, lipsync_wav, calibrate"` succeeds.

- [ ] **Step 8: Commit** `motor_test/talk_settings.py motor_test/lip_sync.py motor_test/talk_loop.py main.py lipsync_wav.py tests/test_talk_settings.py tests/test_lip_sync.py tests/test_talk_loop.py tests/test_main.py tests/test_lipsync_wav.py` — "Lip sync takes the mouth's voltages from poses.toml"

---

### Task 5: Per-motor driver

**Files:**
- Create: `motor_test/motor_driver.py`, `tests/test_motor_driver.py`

**Interfaces:**
- Consumes: `MotorProfile` (Task 3), `TICK_S`, `whole_steps`; `tests.profiles`.
- Produces: `motor_test.motor_driver`: `Rest(mode: str)` (frozen dataclass), `Drive = float | Rest`, `MotorDriver(profile)` with `.update(target: float | None) -> Drive` and `.max_hold_tripped: bool`.

- [ ] **Step 1: Failing tests** — `tests/test_motor_driver.py`:

```python
import pytest

from motor_test.motor_driver import MotorDriver, Rest
from tests.profiles import profile

BRAKE = Rest("brake")


def run(driver, targets):
    return [driver.update(target) for target in targets]


def test_no_command_rests_braked():
    assert run(MotorDriver(profile("hand")), [None, None]) == [BRAKE, BRAKE]


def test_coast_profile_rests_coasting():
    assert MotorDriver(profile("hand", rest="coast")).update(None) == Rest("coast")


def test_zero_volts_means_rest():
    assert MotorDriver(profile("hand")).update(0.0) == BRAKE


def test_driving_harder_is_slew_limited():
    # 24 V/s × 0.02 s = 0.48 V per tick.
    assert run(MotorDriver(profile("hand")), [2.0] * 5) == pytest.approx([0.48, 0.96, 1.44, 1.92, 2.0])


def test_easing_off_is_not_slew_limited():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))
    assert run(driver, [2.0, 1.0]) == [2.0, 1.0]


def test_reversing_slews_through_zero():
    driver = MotorDriver(profile("pivot", slew_v_per_s=50.0))  # 1 V per tick
    assert run(driver, [2.0, 2.0, -2.0, -2.0, -2.0, -2.0]) == pytest.approx([1.0, 2.0, 1.0, 0.0, -1.0, -2.0])


def test_max_hold_trips_then_rests_and_stays_rested_while_commands_continue():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))  # max_hold 1.0 s = 50 ticks
    held = run(driver, [2.0] * 50)
    assert held == [2.0] * 50 and not driver.max_hold_tripped
    assert run(driver, [2.0] * 3) == [BRAKE] * 3
    assert driver.max_hold_tripped


def test_max_hold_resets_once_commands_stop():
    driver = MotorDriver(profile("hand", slew_v_per_s=1000.0))
    run(driver, [2.0] * 51)
    driver.update(None)
    assert not driver.max_hold_tripped
    assert driver.update(2.0) == 2.0


def test_rest_pulse_plays_on_the_way_to_rest():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))  # rest pulse +0.5 V for 0.08 s = 4 ticks
    assert run(driver, [-3.0, None, None, None, None, None]) == [-3.0, 0.5, 0.5, 0.5, 0.5, BRAKE]


def test_no_pulse_when_already_resting():
    assert run(MotorDriver(profile("mouth")), [None, None]) == [BRAKE, BRAKE]


def test_new_command_interrupts_the_rest_pulse():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    assert run(driver, [-3.0, None, -2.0]) == [-3.0, 0.5, -2.0]


def test_tripping_max_hold_plays_the_rest_pulse():
    driver = MotorDriver(profile("mouth", slew_v_per_s=1000.0))
    run(driver, [-3.0] * 50)
    assert run(driver, [-3.0] * 5) == [0.5, 0.5, 0.5, 0.5, BRAKE]
```

Run → `ModuleNotFoundError`.

- [ ] **Step 2: Implement `motor_test/motor_driver.py`**

```python
"""Turns one motor's commanded volts into its drive for each 20 ms tick, within its limits.

Driving harder is slew-limited; driving away from rest longer than max_hold_s forces a rest
(stall protection) until commands stop; going to rest plays the motor's rest pulse, then brakes
or coasts. See "Command board" in SPEC.md.
"""

from dataclasses import dataclass

from motor_test.pcm import TICK_S
from motor_test.poses import MotorProfile
from motor_test.ramp import whole_steps


@dataclass(frozen=True)
class Rest:
    """Not driven: "brake" shorts the leads, "coast" leaves them open."""

    mode: str


Drive = float | Rest


class MotorDriver:
    def __init__(self, profile: MotorProfile):
        self._profile = profile
        self._slew_per_tick = profile.slew_v_per_s * TICK_S
        self._max_hold_ticks = whole_steps(profile.max_hold_s, TICK_S)
        self._pulse_ticks = whole_steps(profile.rest_pulse_s, TICK_S) if profile.rest_pulse_s > 0 else 0
        self._volts = 0.0
        self._held_ticks = 0
        self._pulse_left = 0
        self.max_hold_tripped = False

    def update(self, target: float | None) -> Drive:
        """Advance one tick toward `target` volts (None or 0 = rest) and return the drive."""
        if target is None or target == 0.0:
            self.max_hold_tripped = False
            return self._to_rest()
        if self.max_hold_tripped:
            return self._to_rest()
        self._pulse_left = 0
        self._volts = self._slewed(target)
        self._held_ticks += 1
        if self._held_ticks > self._max_hold_ticks:
            self.max_hold_tripped = True
            return self._to_rest()
        return self._volts

    def _slewed(self, target: float) -> float:
        current = self._volts
        easing_off = abs(target) <= abs(current) and (target >= 0) == (current >= 0)
        if easing_off:
            return target
        step = max(-self._slew_per_tick, min(self._slew_per_tick, target - current))
        return current + step

    def _to_rest(self) -> Drive:
        if self._volts != 0.0:
            self._volts = 0.0
            self._held_ticks = 0
            self._pulse_left = self._pulse_ticks
        if self._pulse_left > 0:
            self._pulse_left -= 1
            return self._profile.rest_pulse_v
        return Rest(self._profile.rest)
```

Note for `test_reversing_slews_through_zero`: from 2.0 toward −2.0 the target has the other sign, so it is not "easing off" and steps by 1 V per tick through 0 (the TB6612 adapter already zeroes duty on a direction change).

- [ ] **Step 3: Run** `.venv/bin/pytest tests/test_motor_driver.py -v && .venv/bin/pytest -q` → all pass.

- [ ] **Step 4: Commit** `motor_test/motor_driver.py tests/test_motor_driver.py` — "Add the per-motor driver: slew, max hold, rest pulse, brake or coast"

---

### Task 6: Command board

**Files:**
- Create: `motor_test/control_board.py`, `tests/test_control_board.py`
- Modify: `tests/fakes.py` (add `FakeClock`)

**Interfaces:**
- Consumes: `MOTOR_NAMES`, `motor_spec` (Task 2); `MotorProfile` (Task 3).
- Produces: `motor_test.control_board`: `MOUTH_MODES = ("live", "show")`, `ControlBoard(profiles, timeout_s: float, mouth_mode: str, clock: Callable[[], float])` with `mouth_mode` (property), `set_mouth_mode(mode)`, `set_value(motor, value)`, `start_pose(motor, name, seconds: float | None = None)`, `rest(motor)`, `rest_all()`, `target_volts(motor) -> float | None`, `report(motor, volts: float, max_hold_tripped: bool)`, `status(mumble_connected: bool) -> dict`.
- Produces: `tests.fakes.FakeClock` (callable; `.now`; `.advance(seconds)`).

- [ ] **Step 1: FakeClock** — append to `tests/fakes.py`:

```python
class FakeClock:
    """A clock that only moves when told to, for timing rules without sleeping."""

    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
```

- [ ] **Step 2: Failing tests** — `tests/test_control_board.py`:

```python
import threading

import pytest

from motor_test.control_board import ControlBoard
from tests.fakes import FakeClock
from tests.profiles import PROFILES


def board(mode="live", timeout_s=0.5):
    clock = FakeClock()
    return ControlBoard(PROFILES, timeout_s, mode, clock), clock


def test_no_command_means_rest():
    b, _ = board()
    assert b.target_volts("hand") is None


def test_value_maps_through_the_motor_profile():
    b, _ = board()
    b.set_value("hand", 1.0)
    assert b.target_volts("hand") == 2.0
    b.set_value("pivot", -1.0)
    assert b.target_volts("pivot") == -2.0


def test_value_holds_until_the_timeout_then_rests():
    b, clock = board()
    b.set_value("hand", 1.0)
    clock.advance(0.5)
    assert b.target_volts("hand") == 2.0
    clock.advance(0.01)
    assert b.target_volts("hand") is None


def test_each_new_value_restarts_the_timeout():
    b, clock = board()
    b.set_value("hand", 1.0)
    clock.advance(0.4)
    b.set_value("hand", 0.5)
    clock.advance(0.4)
    assert b.target_volts("hand") == 1.5


def test_pose_plays_for_its_default_duration():
    b, clock = board()
    b.start_pose("elbow", "up")
    clock.advance(0.4)
    assert b.target_volts("elbow") == 2.0
    clock.advance(0.1)  # 0.4 + 0.1 is exactly 0.5 in binary floating point
    assert b.target_volts("elbow") is None


def test_pose_duration_can_be_given():
    b, clock = board()
    b.start_pose("elbow", "up", 2.0)
    clock.advance(1.9)
    assert b.target_volts("elbow") == 2.0


def test_unknown_pose_is_rejected():
    b, _ = board()
    with pytest.raises(ValueError, match="wave"):
        b.start_pose("hand", "wave")


def test_new_command_replaces_the_old_one():
    b, _ = board()
    b.start_pose("pivot", "left", 5.0)
    b.set_value("pivot", 1.0)
    assert b.target_volts("pivot") == 2.0


def test_rest_and_rest_all():
    b, _ = board()
    b.set_value("hand", 1.0)
    b.set_value("elbow", 1.0)
    b.rest("hand")
    assert b.target_volts("hand") is None and b.target_volts("elbow") == 2.0
    b.rest_all()
    assert b.target_volts("elbow") is None


def test_mouth_mode_switches_and_is_validated():
    b, _ = board()
    assert b.mouth_mode == "live"
    b.set_mouth_mode("show")
    assert b.mouth_mode == "show"
    with pytest.raises(ValueError):
        b.set_mouth_mode("auto")
    with pytest.raises(ValueError):
        ControlBoard(PROFILES, 0.5, "auto", FakeClock())
    with pytest.raises(ValueError):
        ControlBoard(PROFILES, 0.0, "live", FakeClock())


def test_status_reports_mode_commands_reports_and_motor_facts():
    b, clock = board()
    b.set_value("hand", 0.5)
    b.start_pose("elbow", "up")
    clock.advance(0.2)
    b.report("hand", 1.5, False)
    status = b.status(mumble_connected=True)
    assert status["mouth_mode"] == "live" and status["mumble_connected"] is True
    assert status["motors"]["hand"]["command"] == {"value": 0.5, "age_s": 0.2}
    assert status["motors"]["elbow"]["command"] == {"pose": "up", "seconds_left": 0.3}
    assert status["motors"]["hand"]["volts"] == 1.5
    assert status["motors"]["hand"]["max_hold_tripped"] is False
    assert status["motors"]["pivot"]["two_sided"] is True
    assert status["motors"]["pivot"]["poses"] == ["left", "right"]
    assert status["motors"]["mouth"]["calibrated"] is True
    assert status["motors"]["mouth"]["command"] is None


def test_concurrent_writers_and_reader_see_whole_commands():
    b, _ = board()
    stop = threading.Event()
    seen = set()

    def write(value):
        while not stop.is_set():
            b.set_value("hand", value)

    def read():
        for _ in range(5000):
            seen.add(b.target_volts("hand"))

    writers = [threading.Thread(target=write, args=(v,)) for v in (0.0, 1.0)]
    for thread in writers:
        thread.start()
    read()
    stop.set()
    for thread in writers:
        thread.join()
    assert seen <= {None, 0.0, 2.0}
```

Run → `ModuleNotFoundError`.

- [ ] **Step 3: Implement `motor_test/control_board.py`**

```python
"""The latest show-control command for each motor, shared by the network threads and the talk loop.

OSC and HTTP threads write; the talk loop reads each motor's target once per tick and reports
back what it drove, for /status. A value holds until `timeout_s` passes without a new one
(dead-man); a pose holds for its duration. See "Command board" in SPEC.md.
"""

import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from motor_test.motors import MOTOR_NAMES, motor_spec
from motor_test.poses import MotorProfile

MOUTH_MODES = ("live", "show")


@dataclass(frozen=True)
class _Value:
    value: float
    received_at: float


@dataclass(frozen=True)
class _Pose:
    name: str
    started_at: float
    seconds: float


class ControlBoard:
    def __init__(self, profiles: Mapping[str, MotorProfile], timeout_s: float, mouth_mode: str, clock: Callable[[], float]):
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError(f"control timeout must be a positive number of seconds, got {timeout_s}")
        _check_mode(mouth_mode)
        self._profiles = profiles
        self._timeout_s = timeout_s
        self._clock = clock
        self._lock = threading.Lock()
        self._mouth_mode = mouth_mode
        self._commands: dict[str, _Value | _Pose] = {}
        self._reports: dict[str, tuple[float, bool]] = {name: (0.0, False) for name in MOTOR_NAMES}

    @property
    def mouth_mode(self) -> str:
        with self._lock:
            return self._mouth_mode

    def set_mouth_mode(self, mode: str) -> None:
        _check_mode(mode)
        with self._lock:
            self._mouth_mode = mode

    def set_value(self, motor: str, value: float) -> None:
        with self._lock:
            self._commands[motor] = _Value(value, self._clock())

    def start_pose(self, motor: str, name: str, seconds: float | None = None) -> None:
        pose = self._profiles[motor].poses.get(name)
        if pose is None:
            raise ValueError(f"unknown pose {name!r} for {motor}")
        with self._lock:
            self._commands[motor] = _Pose(name, self._clock(), pose.seconds if seconds is None else seconds)

    def rest(self, motor: str) -> None:
        with self._lock:
            self._commands.pop(motor, None)

    def rest_all(self) -> None:
        with self._lock:
            self._commands.clear()

    def target_volts(self, motor: str) -> float | None:
        """The volts `motor` is commanded to now, or None when it should rest."""
        with self._lock:
            command = self._current(motor, self._clock())
        if command is None:
            return None
        if isinstance(command, _Value):
            return self._profiles[motor].volts_for(command.value)
        return self._profiles[motor].poses[command.name].volts

    def report(self, motor: str, volts: float, max_hold_tripped: bool) -> None:
        """Record what the talk loop drove, for /status."""
        with self._lock:
            self._reports[motor] = (volts, max_hold_tripped)

    def status(self, mumble_connected: bool) -> dict:
        with self._lock:
            now = self._clock()
            motors = {}
            for name in MOTOR_NAMES:
                volts, tripped = self._reports[name]
                profile = self._profiles[name]
                motors[name] = {
                    "command": _describe(self._current(name, now), now),
                    "volts": volts,
                    "max_hold_tripped": tripped,
                    "calibrated": profile.calibrated,
                    "two_sided": motor_spec(name).two_sided,
                    "poses": sorted(profile.poses),
                }
            return {"mouth_mode": self._mouth_mode, "mumble_connected": mumble_connected, "motors": motors}

    def _current(self, motor: str, now: float) -> _Value | _Pose | None:
        """The motor's command if still in force, dropping it once expired. Caller holds the lock."""
        command = self._commands.get(motor)
        expired = (isinstance(command, _Value) and now - command.received_at > self._timeout_s) or (
            isinstance(command, _Pose) and now - command.started_at >= command.seconds
        )
        if expired:
            del self._commands[motor]
            return None
        return command


def _check_mode(mode: str) -> None:
    if mode not in MOUTH_MODES:
        raise ValueError(f"mouth mode must be one of {', '.join(MOUTH_MODES)}, got {mode!r}")


def _describe(command: _Value | _Pose | None, now: float) -> dict | None:
    if command is None:
        return None
    if isinstance(command, _Value):
        return {"value": command.value, "age_s": round(now - command.received_at, 3)}
    return {"pose": command.name, "seconds_left": round(command.started_at + command.seconds - now, 3)}
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_control_board.py -v && .venv/bin/pytest -q` → all pass (the concurrency test must finish in well under a second).

- [ ] **Step 5: Commit** `motor_test/control_board.py tests/test_control_board.py tests/fakes.py` — "Add the show-control command board with dead-man timeout and pose durations"

---

### Task 7: Command translation and rate-limited logging

**Files:**
- Create: `motor_test/show_commands.py`, `motor_test/rate_limited_log.py`, `tests/test_show_commands.py`, `tests/test_rate_limited_log.py`

**Interfaces:**
- Consumes: `ControlBoard`, `MOUTH_MODES` (Task 6); `MOTOR_NAMES`, `motor_spec` (Task 2); `MotorProfile` (Task 3).
- Produces: `motor_test.rate_limited_log.RateLimitedLog(log, interval_s: float, clock)` callable as `log(key: str, message: str)`.
- Produces: `motor_test.show_commands`: `CommandError(message, status=400)` (`.status`), commands `SetValue(motor, value, requested)`, `StartPose(motor, name, seconds)`, `RestCommand(motor | None)`, `SetMouthMode(mode)`; `osc_command(address, args, profiles) -> Command`; `http_command(path, body: Mapping, profiles) -> Command`; `apply(command, board)`; `handle_osc(address, args, profiles, board, log: RateLimitedLog)`.

- [ ] **Step 1: Failing rate-limit tests** — `tests/test_rate_limited_log.py`:

```python
from motor_test.rate_limited_log import RateLimitedLog
from tests.fakes import FakeClock


def test_one_line_per_key_per_interval():
    lines, clock = [], FakeClock()
    log = RateLimitedLog(lines.append, 60.0, clock)
    log("a", "first")
    log("a", "again")
    log("b", "other")
    clock.advance(60.0)
    log("a", "later")
    assert lines == ["first", "other", "later"]
```

- [ ] **Step 2: Implement `motor_test/rate_limited_log.py`**

```python
"""Logs at most one line per kind per interval, so a misbehaving controller can't flood the journal."""

from collections.abc import Callable


class RateLimitedLog:
    def __init__(self, log: Callable[[str], None], interval_s: float, clock: Callable[[], float]):
        self._log = log
        self._interval_s = interval_s
        self._clock = clock
        self._last: dict[str, float] = {}

    def __call__(self, key: str, message: str) -> None:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._interval_s:
            return
        self._last[key] = now
        self._log(message)
```

Run its test → pass.

- [ ] **Step 3: Failing translation tests** — `tests/test_show_commands.py`:

```python
import math

import pytest

from motor_test.control_board import ControlBoard
from motor_test.rate_limited_log import RateLimitedLog
from motor_test.show_commands import (
    CommandError, RestCommand, SetMouthMode, SetValue, StartPose, apply, handle_osc, http_command, osc_command,
)
from tests.fakes import FakeClock
from tests.profiles import PROFILES


def osc(address, *args):
    return osc_command(address, list(args), PROFILES)


def http(path, body=None):
    return http_command(path, body or {}, PROFILES)


def test_osc_routes():
    assert osc("/jack/hand", 0.5) == SetValue("hand", 0.5, 0.5)
    assert osc("/jack/hand/pose", "curl") == StartPose("hand", "curl", None)
    assert osc("/jack/hand/pose", "curl", 2) == StartPose("hand", "curl", 2.0)
    assert osc("/jack/hand/rest") == RestCommand("hand")
    assert osc("/jack/rest") == RestCommand(None)
    assert osc("/jack/mouth/mode", "show") == SetMouthMode("show")


def test_http_routes_match_osc():
    assert http("/hand", {"value": 0.5}) == SetValue("hand", 0.5, 0.5)
    assert http("/hand/pose", {"name": "curl", "seconds": 2}) == StartPose("hand", "curl", 2.0)
    assert http("/hand/rest") == RestCommand("hand")
    assert http("/rest") == RestCommand(None)
    assert http("/mouth/mode", {"mode": "live"}) == SetMouthMode("live")


def test_values_are_clamped_to_the_motors_range():
    assert osc("/jack/hand", 1.4) == SetValue("hand", 1.0, 1.4)
    assert osc("/jack/hand", -0.2) == SetValue("hand", 0.0, -0.2)
    assert osc("/jack/pivot", -1.5) == SetValue("pivot", -1.0, -1.5)


@pytest.mark.parametrize(
    "address, args, status",
    [
        ("/jack/tail", [0.5], 404),
        ("/other/hand", [0.5], 404),
        ("/jack/hand/wave", [], 404),
        ("/jack/hand/pose", ["wave"], 404),
        ("/jack/hand", [], 400),
        ("/jack/hand", ["half"], 400),
        ("/jack/hand", [True], 400),
        ("/jack/hand", [math.nan], 400),
        ("/jack/hand", [0.5, 0.6], 400),
        ("/jack/hand/pose", [], 400),
        ("/jack/hand/pose", ["curl", 0], 400),
        ("/jack/mouth/mode", ["auto"], 400),
        ("/jack/rest", [1], 400),
    ],
)
def test_bad_osc_is_rejected_with_a_status(address, args, status):
    with pytest.raises(CommandError) as error:
        osc_command(address, args, PROFILES)
    assert error.value.status == status


def test_unexpected_http_fields_are_rejected():
    with pytest.raises(CommandError) as error:
        http("/hand", {"value": 0.5, "speed": 2})
    assert error.value.status == 400 and "speed" in str(error.value)


def board(mode="live"):
    return ControlBoard(PROFILES, 0.5, mode, FakeClock())


def test_apply_drives_the_board():
    b = board()
    apply(SetValue("hand", 1.0, 1.0), b)
    assert b.target_volts("hand") == 2.0
    apply(StartPose("elbow", "up", None), b)
    assert b.target_volts("elbow") == 2.0
    apply(RestCommand(None), b)
    assert b.target_volts("hand") is None
    apply(SetMouthMode("show"), b)
    assert b.mouth_mode == "show"


@pytest.mark.parametrize("command", [SetValue("mouth", 0.5, 0.5), StartPose("mouth", "open", None)])
def test_mouth_commands_conflict_with_live_mode(command):
    b = board("live")
    with pytest.raises(CommandError) as error:
        apply(command, b)
    assert error.value.status == 409
    assert b.target_volts("mouth") is None


def test_mouth_commands_work_in_show_mode():
    b = board("show")
    apply(SetValue("mouth", 1.0, 1.0), b)
    assert b.target_volts("mouth") == -6.0


def test_handle_osc_applies_good_messages_and_logs_bad_ones_rate_limited():
    b, lines, clock = board(), [], FakeClock()
    log = RateLimitedLog(lines.append, 60.0, clock)
    handle_osc("/jack/hand", [1.0], PROFILES, b, log)
    assert b.target_volts("hand") == 2.0
    handle_osc("/jack/hand", ["oops"], PROFILES, b, log)
    handle_osc("/jack/hand", ["oops"], PROFILES, b, log)
    handle_osc("/jack/mouth", [0.5], PROFILES, b, log)
    handle_osc("/jack/hand", [3.0], PROFILES, b, log)
    assert len(lines) == 3
    assert "Ignored OSC /jack/hand" in lines[0]
    assert "live mode" in lines[1]
    assert "Clamped OSC /jack/hand 3.0 to 1.0" in lines[2]
```

Run → `ModuleNotFoundError`.

- [ ] **Step 4: Implement `motor_test/show_commands.py`**

```python
"""Turns OSC messages and HTTP requests into command-board actions, with one set of rules for both.

Routes (an OSC address below /jack, or an HTTP path): /<motor> value; /<motor>/pose name [seconds];
/<motor>/rest; /rest; /mouth/mode live|show. See "OSC" and "HTTP" in SPEC.md.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from motor_test.control_board import MOUTH_MODES, ControlBoard
from motor_test.motors import MOTOR_NAMES, motor_spec
from motor_test.poses import MotorProfile
from motor_test.rate_limited_log import RateLimitedLog

OSC_PREFIX = "/jack"


class CommandError(ValueError):
    """A command that can't be carried out; `status` is the HTTP status that says why."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SetValue:
    motor: str
    value: float
    requested: float  # as sent, before clamping


@dataclass(frozen=True)
class StartPose:
    motor: str
    name: str
    seconds: float | None


@dataclass(frozen=True)
class RestCommand:
    motor: str | None  # None rests every motor


@dataclass(frozen=True)
class SetMouthMode:
    mode: str


Command = SetValue | StartPose | RestCommand | SetMouthMode


def osc_command(address: str, args: Sequence[object], profiles: Mapping[str, MotorProfile]) -> Command:
    if not address.startswith(OSC_PREFIX + "/"):
        raise CommandError(f"unknown address {address!r}", 404)
    route = address[len(OSC_PREFIX):]
    names = _argument_names(_parts(route))
    # Build first, so an unknown address is a 404 even when it carries arguments.
    command = _build(route, dict(zip(names, args)), profiles)
    if len(args) > len(names):
        raise CommandError(f"too many arguments for {address}")
    return command


def http_command(path: str, body: Mapping[str, object], profiles: Mapping[str, MotorProfile]) -> Command:
    return _build(path, dict(body), profiles)


def apply(command: Command, board: ControlBoard) -> None:
    """Carry out `command`; a mouth move while the mouth follows the live voice is a 409 conflict."""
    if isinstance(command, SetValue | StartPose) and command.motor == "mouth" and board.mouth_mode == "live":
        raise CommandError("the mouth is in live mode; switch it with /mouth/mode show first", 409)
    if isinstance(command, SetValue):
        board.set_value(command.motor, command.value)
    elif isinstance(command, StartPose):
        board.start_pose(command.motor, command.name, command.seconds)
    elif isinstance(command, RestCommand):
        if command.motor is None:
            board.rest_all()
        else:
            board.rest(command.motor)
    else:
        board.set_mouth_mode(command.mode)


def handle_osc(
    address: str, args: Sequence[object], profiles: Mapping[str, MotorProfile], board: ControlBoard, log: RateLimitedLog
) -> None:
    """Apply one OSC message; anything that can't be applied is logged (rate-limited) and dropped."""
    try:
        command = osc_command(address, args, profiles)
        apply(command, board)
    except CommandError as error:
        log(f"ignored {address}", f"Ignored OSC {address} {list(args)}: {error}")
        return
    if isinstance(command, SetValue) and command.value != command.requested:
        log(f"clamped {command.motor}", f"Clamped OSC {address} {command.requested} to {command.value}")


def _parts(route: str) -> list[str]:
    return [part for part in route.split("/") if part]


def _argument_names(parts: list[str]) -> list[str]:
    """OSC arguments are positional; these are their names, matching the HTTP JSON fields."""
    if parts == ["mouth", "mode"]:
        return ["mode"]
    if len(parts) == 2 and parts[1] == "pose":
        return ["name", "seconds"]
    if len(parts) == 1 and parts[0] in MOTOR_NAMES:
        return ["value"]
    return []


def _build(route: str, params: dict, profiles: Mapping[str, MotorProfile]) -> Command:
    parts = _parts(route)
    if parts == ["rest"]:
        _only(params, set())
        return RestCommand(None)
    if parts == ["mouth", "mode"]:
        _only(params, {"mode"})
        mode = params.get("mode")
        if mode not in MOUTH_MODES:
            raise CommandError(f"mode must be one of {', '.join(MOUTH_MODES)}, got {mode!r}")
        return SetMouthMode(mode)
    if not parts or parts[0] not in MOTOR_NAMES:
        raise CommandError(f"unknown motor or route {route!r}; motors: {', '.join(MOTOR_NAMES)}", 404)
    motor, rest_of_route = parts[0], parts[1:]
    if rest_of_route == []:
        _only(params, {"value"})
        requested = _number(params, "value")
        low = -1.0 if motor_spec(motor).two_sided else 0.0
        return SetValue(motor, min(1.0, max(low, requested)), requested)
    if rest_of_route == ["pose"]:
        _only(params, {"name", "seconds"})
        name = params.get("name")
        if not isinstance(name, str):
            raise CommandError("pose needs a name")
        if name not in profiles[motor].poses:
            raise CommandError(f"unknown pose {name!r} for {motor}; poses: {', '.join(sorted(profiles[motor].poses))}", 404)
        seconds = None if params.get("seconds") is None else _number(params, "seconds")
        if seconds is not None and seconds <= 0:
            raise CommandError(f"seconds must be positive, got {seconds}")
        return StartPose(motor, name, seconds)
    if rest_of_route == ["rest"]:
        _only(params, set())
        return RestCommand(motor)
    raise CommandError(f"unknown route {route!r}", 404)


def _only(params: dict, allowed: set[str]) -> None:
    unexpected = set(params) - allowed
    if unexpected:
        raise CommandError(f"unexpected {', '.join(sorted(unexpected))}")


def _number(params: dict, key: str) -> float:
    if key not in params:
        raise CommandError(f"{key} is required")
    value = params[key]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise CommandError(f"{key} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise CommandError(f"{key} must be a finite number, got {value!r}")
    return float(value)
```

- [ ] **Step 5: Run** `.venv/bin/pytest tests/test_show_commands.py tests/test_rate_limited_log.py -v && .venv/bin/pytest -q` → all pass.

- [ ] **Step 6: Commit** `motor_test/show_commands.py motor_test/rate_limited_log.py tests/test_show_commands.py tests/test_rate_limited_log.py` — "Translate OSC and HTTP into show-control commands with one set of rules"

---

### Task 8: The talk loop drives all four motors

**Files:**
- Modify: `motor_test/talk_loop.py`, `main.py` (call site only), `lipsync_wav.py` (call site only)
- Test: `tests/test_talk_loop.py` (rewrite)

**Interfaces:**
- Consumes: `MotorDriver`, `Rest`, `Drive` (Task 5); `ControlBoard` (Task 6); `MouthController(settings, mouth)` (Task 4); `MotorOutput.coast` (Task 2).
- Produces: `run_talk_loop(sources, sink, motors: Mapping[str, MotorOutput], profiles: Mapping[str, MotorProfile], settings, board: ControlBoard, supply_volts, on_second, until=lambda: False) -> None`. `motors` must contain `"mouth"`; any subset of the other names is allowed.

- [ ] **Step 1: Rewrite `tests/test_talk_loop.py`**

```python
import pytest

from motor_test.control_board import ControlBoard
from motor_test.pcm import TICKS_PER_SECOND, silence
from motor_test.ramp import volts_to_count
from motor_test.talk_loop import run_talk_loop
from motor_test.talk_settings import TalkSettings
from tests.audio import constant_frame
from tests.fakes import FakeClock, MotorThatFailsToStop, RecordingMotor, RecordingSink, ScriptedSource, drives, no_op
from tests.profiles import PROFILES, profile

SUPPLY_VOLTS = 12.0
LOUD = constant_frame(20000)  # about -4.3 dBFS: above full_db once the envelope has risen
FAST = {**PROFILES, "mouth": profile("mouth", slew_v_per_s=1000.0), "hand": profile("hand", slew_v_per_s=1000.0),
        "elbow": profile("elbow", slew_v_per_s=1000.0)}


def stop_after(ticks):
    """An `until` check that lets the loop run exactly `ticks` ticks."""
    remaining = ticks

    def until():
        nonlocal remaining
        if remaining == 0:
            return True
        remaining -= 1
        return False

    return until


def four_motors(**replacements):
    motors = {name: RecordingMotor() for name in ("mouth", "hand", "pivot", "elbow")}
    motors.update(replacements)
    return motors


def new_board(mode="live", profiles=FAST):
    return ControlBoard(profiles, 0.5, mode, FakeClock())


def talk(sources, ticks, *, board=None, motors=None, sink=None, profiles=FAST, settings=TalkSettings(), on_second=no_op):
    sink = RecordingSink() if sink is None else sink
    motors = four_motors() if motors is None else motors
    board = new_board(profiles=profiles) if board is None else board
    run_talk_loop(sources, sink, motors, profiles, settings, board, SUPPLY_VOLTS, on_second, stop_after(ticks))
    return sink, motors, board


def test_quiet_sources_play_silence_and_keep_the_mouth_closed():
    sink, motors, _ = talk([ScriptedSource([])], ticks=3)
    assert sink.frames == [silence()] * 3
    assert drives(motors["mouth"]) == [0]


def test_uncommanded_motors_brake_once_then_again_on_exit():
    _, motors, _ = talk([ScriptedSource([])], ticks=3)
    assert motors["hand"].calls == [("stop",), ("stop",)]


def test_a_loud_voice_is_played_and_opens_the_mouth_fully_in_live_mode():
    sink, motors, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2)
    assert sink.frames == [LOUD, LOUD]
    assert drives(motors["mouth"])[0] < 0
    assert drives(motors["mouth"])[1] == volts_to_count(-6.0, SUPPLY_VOLTS)


def test_sources_sounding_together_are_mixed():
    sink, _, _ = talk([ScriptedSource([LOUD]), ScriptedSource([LOUD])], ticks=1)
    assert sink.frames == [constant_frame(32767)]


def test_mouth_lead_delays_the_audio_but_not_the_mouth():
    sink, motors, _ = talk([ScriptedSource([LOUD] * 3)], ticks=3, settings=TalkSettings(mouth_lead_ms=40.0))
    assert sink.frames == [silence(), silence(), LOUD]
    assert drives(motors["mouth"])[0] < 0


@pytest.mark.parametrize("ticks, pings", [(TICKS_PER_SECOND - 1, 0), (TICKS_PER_SECOND, 1), (2 * TICKS_PER_SECOND, 2)])
def test_on_second_runs_once_per_second_of_audio(ticks, pings):
    calls = []
    talk([ScriptedSource([])], ticks=ticks, on_second=lambda: calls.append(1))
    assert len(calls) == pings


def test_a_commanded_value_drives_its_motor_and_a_steady_value_is_written_once():
    board = new_board()
    board.set_value("hand", 1.0)
    _, motors, _ = talk([ScriptedSource([])], ticks=5, board=board)
    assert drives(motors["hand"]) == [volts_to_count(2.0, SUPPLY_VOLTS)]


def test_a_pose_drives_its_volts():
    board = new_board()
    board.start_pose("elbow", "up")
    _, motors, _ = talk([ScriptedSource([])], ticks=2, board=board)
    assert drives(motors["elbow"]) == [volts_to_count(2.0, SUPPLY_VOLTS)]


def test_show_mode_mouth_follows_the_board_not_the_voice():
    board = new_board("show")
    board.set_value("mouth", 1.0)
    _, motors, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2, board=board)
    assert drives(motors["mouth"]) == [volts_to_count(-6.0, SUPPLY_VOLTS)]


def test_live_mode_mouth_ignores_the_board():
    board = new_board("live")
    board.set_value("mouth", 1.0)
    _, motors, _ = talk([ScriptedSource([])], ticks=2, board=board)
    assert drives(motors["mouth"]) == [0]


def test_a_coast_profile_coasts_at_rest():
    profiles = {**FAST, "hand": profile("hand", rest="coast")}
    _, motors, _ = talk([ScriptedSource([])], ticks=2, profiles=profiles)
    assert motors["hand"].calls[0] == ("coast",)


def test_what_was_driven_is_reported_to_the_board():
    board = new_board()
    board.set_value("hand", 1.0)
    talk([ScriptedSource([])], ticks=2, board=board)
    assert board.status(False)["motors"]["hand"]["volts"] == 2.0


def test_a_sound_card_failure_brakes_every_motor_and_closes_the_sink():
    sink, motors = RecordingSink(fail_on_write=2), four_motors()
    with pytest.raises(OSError):
        talk([ScriptedSource([LOUD] * 5)], ticks=5, sink=sink, motors=motors)
    assert all(motor.calls[-1] == ("stop",) for motor in motors.values())
    assert sink.closed


def test_a_motor_failing_to_stop_still_stops_the_others_and_closes_the_sink():
    sink, motors = RecordingSink(), four_motors(mouth=MotorThatFailsToStop())
    with pytest.raises(OSError):
        talk([ScriptedSource([])], ticks=1, sink=sink, motors=motors)
    assert all(motors[name].calls[-1] == ("stop",) for name in ("hand", "pivot", "elbow"))
    assert sink.closed
```

Run → fails (old signature).

- [ ] **Step 2: Implement** — replace `run_talk_loop` in `motor_test/talk_loop.py` (keep the module docstring, extend it: "…and drives every motor: the mouth from the voice in live mode, everything else from the show-control board."):

```python
from collections import deque
from collections.abc import Callable, Mapping, Sequence

from motor_test.attempt_all import attempt_all
from motor_test.control_board import ControlBoard
from motor_test.envelope import EnvelopeFollower, rms_dbfs
from motor_test.lip_sync import MouthController
from motor_test.motor_driver import Drive, MotorDriver, Rest
from motor_test.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from motor_test.ports import AudioSink, MotorOutput, VoiceSource
from motor_test.poses import MotorProfile
from motor_test.ramp import volts_to_count
from motor_test.talk_settings import TalkSettings


def run_talk_loop(
    sources: Sequence[VoiceSource],
    sink: AudioSink,
    motors: Mapping[str, MotorOutput],
    profiles: Mapping[str, MotorProfile],
    settings: TalkSettings,
    board: ControlBoard,
    supply_volts: float,
    on_second: Callable[[], None],
    until: Callable[[], bool] = lambda: False,
) -> None:
    """Play every source and drive every motor until `until()` is true (never, for the app).

    Each tick mixes the next frame from every source (silence if none are sounding). The mouth
    follows that frame's smoothed loudness in live mode, or the board in show mode; every other
    motor follows the board through its MotorDriver. A motor is written only when its drive
    changes. The frame plays `mouth_lead_ticks` ticks later so the mouth can run ahead of the
    sound. `on_second` runs once per second of audio. Every motor is stopped and the sink closed
    on the way out, whatever the reason, and a failure in one of those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    lip_sync = MouthController(settings, profiles["mouth"])
    drivers = {name: MotorDriver(profiles[name]) for name in motors}
    applied: dict[str, Drive | None] = {name: None for name in motors}
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    ticks = 0
    try:
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            live_mouth = lip_sync.update(envelope.update(rms_dbfs(frame)))
            for name, motor in motors.items():
                if name == "mouth" and board.mouth_mode == "live":
                    drive, tripped = live_mouth, False
                else:
                    drive = drivers[name].update(board.target_volts(name))
                    tripped = drivers[name].max_hold_tripped
                if drive != applied[name]:
                    _apply(motor, drive, supply_volts)
                    applied[name] = drive
                board.report(name, 0.0 if isinstance(drive, Rest) else drive, tripped)
            delayed_audio.append(frame)
            sink.write(delayed_audio.popleft())
            ticks += 1
            if ticks % TICKS_PER_SECOND == 0:
                on_second()
    finally:
        attempt_all([*(motor.stop for motor in motors.values()), sink.close])


def _apply(motor: MotorOutput, drive: Drive, supply_volts: float) -> None:
    if drive == Rest("coast"):
        motor.coast()
    elif isinstance(drive, Rest):
        motor.stop()
    else:
        motor.drive(volts_to_count(drive, supply_volts))
```

- [ ] **Step 3: Call sites (interim, Task 11 finishes composition).** In `main.main()` and `lipsync_wav.main()`, build `motors = {"mouth": Tb6612Motor(chip, MOTOR_B), "hand": Tb6612Motor(chip, MOTOR_A)}` from the existing single chip, a `ControlBoard(profiles, 0.5, "live", time.monotonic)` (import `time`; `lipsync_wav` loads all profiles with `load_profiles(POSES_PATHS, SUPPLY_VOLTS)` and takes `["mouth"]` from them where it needs it), and call `run_talk_loop([source(s)], sink, motors, profiles, settings, board, SUPPLY_VOLTS, <on_second>[, <until>])`. Keep the existing "Talking: …" print.

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_talk_loop.py -v && .venv/bin/pytest -q && .venv/bin/python -c "import main, lipsync_wav"` → all pass.

- [ ] **Step 5: Commit** `motor_test/talk_loop.py main.py lipsync_wav.py tests/test_talk_loop.py` — "Drive every motor from the talk loop: mouth by voice or board, the rest by board"

---

### Task 9: OSC server

Before dispatch, the controller installs the dev dependency: `.venv/bin/pip install python-osc==1.10.2` (network access needed).

**Files:**
- Create: `motor_test/osc_server.py`, `tests/test_osc_server.py`
- Modify: `requirements-dev.txt`, `requirements-pi.txt`

**Interfaces:**
- Produces: `motor_test.osc_server.start_osc_server(host: str, port: int, handle: Callable[[str, list], None]) -> ThreadingOSCUDPServer` (serving on a daemon thread; caller may `shutdown()`/`server_close()`).

- [ ] **Step 1: Requirements** — `requirements-dev.txt` add a line `python-osc==1.10.2`. `requirements-pi.txt` add, after the pymumble line:

```text
# OSC show control (SPEC.md "OSC"); no dependencies of its own.
python-osc==1.10.2
```

- [ ] **Step 2: Failing tests** — `tests/test_osc_server.py`:

```python
import threading

from pythonosc.udp_client import SimpleUDPClient

from motor_test.osc_server import start_osc_server


def serve():
    received = []
    arrived = threading.Event()

    def handle(address, args):
        received.append((address, args))
        arrived.set()

    server = start_osc_server("127.0.0.1", 0, handle)
    client = SimpleUDPClient("127.0.0.1", server.server_address[1])
    return server, client, received, arrived


def test_messages_reach_the_handler_with_address_and_arguments():
    server, client, received, arrived = serve()
    try:
        client.send_message("/jack/hand/pose", ["curl", 2.0])
        assert arrived.wait(2.0)
        assert received == [("/jack/hand/pose", ["curl", 2.0])]
    finally:
        server.shutdown()
        server.server_close()


def test_a_garbage_packet_does_not_stop_the_server():
    server, client, received, arrived = serve()
    try:
        client._sock.sendto(b"not osc at all", ("127.0.0.1", server.server_address[1]))
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert received == [("/jack/rest", [])]
    finally:
        server.shutdown()
        server.server_close()
```

Run → `ModuleNotFoundError: motor_test.osc_server`. (If `client._sock` doesn't exist in python-osc 1.10.2, send the garbage bytes with a plain `socket.socket(AF_INET, SOCK_DGRAM)` instead — the point is a non-OSC datagram.) If python-osc prints a traceback/warning for the garbage packet, that output is expected noise from the library; capture it with `capsys`/`caplog` in the test and assert the server still serves, keeping pytest output pristine.

- [ ] **Step 3: Implement `motor_test/osc_server.py`**

```python
"""OSC over UDP into show control; the only module that imports python-osc."""

import threading
from collections.abc import Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer


def start_osc_server(host: str, port: int, handle: Callable[[str, list], None]) -> ThreadingOSCUDPServer:
    """Serve OSC on a daemon thread, passing every message's address and arguments to `handle`."""
    dispatcher = Dispatcher()
    dispatcher.set_default_handler(lambda address, *args: handle(address, list(args)))
    server = ThreadingOSCUDPServer((host, port), dispatcher)
    threading.Thread(target=server.serve_forever, name="osc", daemon=True).start()
    return server
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_osc_server.py -v && .venv/bin/pytest -q` → pass, pristine.

- [ ] **Step 5: Commit** `motor_test/osc_server.py tests/test_osc_server.py requirements-dev.txt requirements-pi.txt` — "Serve OSC on UDP for show control (python-osc 1.10.2)"

---

### Task 10: HTTP server and control page

**Files:**
- Create: `motor_test/http_server.py`, `motor_test/control_page.html`, `tests/test_http_server.py`

**Interfaces:**
- Consumes: `http_command`, `apply`, `CommandError` (Task 7); `ControlBoard` (Task 6).
- Produces: `motor_test.http_server.start_http_server(host, port, board, profiles, mumble_connected: Callable[[], bool]) -> ThreadingHTTPServer` (daemon thread); `MAX_BODY_BYTES = 4096`.

- [ ] **Step 1: Failing tests** — `tests/test_http_server.py`:

```python
import json
import urllib.error
import urllib.request

import pytest

from motor_test.control_board import ControlBoard
from motor_test.http_server import MAX_BODY_BYTES, start_http_server
from tests.fakes import FakeClock
from tests.profiles import PROFILES


@pytest.fixture
def jack():
    board = ControlBoard(PROFILES, 0.5, "live", FakeClock())
    server = start_http_server("127.0.0.1", 0, board, PROFILES, lambda: True)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield board, base
    server.shutdown()
    server.server_close()


def request(base, path, body=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read()


def test_control_page_is_served(jack):
    _, base = jack
    status, kind, body = request(base, "/")
    assert status == 200 and kind.startswith("text/html")
    assert b"<title>" in body and b"/status" in body


def test_status_is_json(jack):
    _, base = jack
    status, kind, body = request(base, "/status")
    data = json.loads(body)
    assert status == 200 and kind == "application/json"
    assert data["mouth_mode"] == "live" and data["mumble_connected"] is True
    assert set(data["motors"]) == {"mouth", "hand", "pivot", "elbow"}


def test_commands_drive_the_board(jack):
    board, base = jack
    assert request(base, "/hand", {"value": 1.0})[0] == 200
    assert board.target_volts("hand") == 2.0
    assert request(base, "/elbow/pose", {"name": "up"})[0] == 200
    assert board.target_volts("elbow") == 2.0
    assert request(base, "/rest", {})[0] == 200
    assert board.target_volts("hand") is None
    assert request(base, "/mouth/mode", {"mode": "show"})[0] == 200
    assert board.mouth_mode == "show"


def test_empty_body_is_allowed_for_rest(jack):
    _, base = jack
    assert request(base, "/hand/rest", raw=b"")[0] == 200


@pytest.mark.parametrize(
    "path, body, raw, status",
    [
        ("/tail", {"value": 1}, None, 404),
        ("/hand/pose", {"name": "wave"}, None, 404),
        ("/hand", {"value": "x"}, None, 400),
        ("/hand", None, b"{not json", 400),
        ("/hand", None, b"[1, 2]", 400),
        ("/mouth", {"value": 0.5}, None, 409),
        ("/hand", None, b"{" + b" " * MAX_BODY_BYTES + b"}", 413),
    ],
)
def test_errors_are_json_with_a_status(jack, path, body, raw, status):
    _, base = jack
    code, kind, payload = request(base, path, body, raw)
    assert code == status and kind == "application/json"
    assert "error" in json.loads(payload)


def test_unknown_get_is_404(jack):
    _, base = jack
    assert request(base, "/nope")[0] == 404
```

Run → `ModuleNotFoundError`.

- [ ] **Step 2: Implement `motor_test/http_server.py`**

```python
"""HTTP show control on Python's ThreadingHTTPServer: JSON commands, /status and the control page.

See "HTTP" in SPEC.md. Request logging is off: a held slider on the control page sends about
20 requests a second, which would flood the journal.
"""

import json
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor_test.control_board import ControlBoard
from motor_test.poses import MotorProfile
from motor_test.show_commands import CommandError, apply, http_command

MAX_BODY_BYTES = 4096
_PAGE_PATH = Path(__file__).resolve().parent / "control_page.html"


def start_http_server(
    host: str,
    port: int,
    board: ControlBoard,
    profiles: Mapping[str, MotorProfile],
    mumble_connected: Callable[[], bool],
) -> ThreadingHTTPServer:
    """Serve HTTP on a daemon thread."""
    page = _PAGE_PATH.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif self.path == "/status":
                self._json(200, board.status(mumble_connected()))
            else:
                self._json(404, {"error": f"no page at {self.path}"})

        def do_POST(self) -> None:
            try:
                apply(http_command(self.path, self._body(), profiles), board)
            except CommandError as error:
                self._json(error.status, {"error": str(error)})
                return
            self._json(200, {"ok": True})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise CommandError(f"request body over {MAX_BODY_BYTES} bytes", 413)
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}
            try:
                body = json.loads(raw)
            except ValueError as error:
                raise CommandError(f"body is not JSON: {error}") from error
            if not isinstance(body, dict):
                raise CommandError("body must be a JSON object")
            return body

        def _json(self, status: int, data: dict) -> None:
            self._send(status, "application/json", json.dumps(data).encode())

        def _send(self, status: int, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
```

Note: for the 413 case the server responds without reading the oversized body; the test client may see a connection reset instead of the response on some platforms. If so, read the body (discarding it) before raising, still answering 413 — keep the test.

- [ ] **Step 3: Write `motor_test/control_page.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jack control</title>
<style>
  :root { --bg: #fafafa; --fg: #1a1a1a; --muted: #666; --card: #fff; --line: #ddd; --accent: #2a6df4; --warn: #c0392b; }
  @media (prefers-color-scheme: dark) { :root { --bg: #121212; --fg: #eee; --muted: #999; --card: #1e1e1e; --line: #333; --accent: #6c9bff; --warn: #ff6b5b; } }
  body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 16px/1.4 system-ui, sans-serif; }
  h1 { font-size: 1.3rem; margin: 0 0 12px; }
  .motor { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
  .motor h2 { font-size: 1.05rem; margin: 0 0 8px; display: flex; justify-content: space-between; gap: 8px; }
  .muted { color: var(--muted); font-weight: normal; font-size: .85rem; }
  .warn { color: var(--warn); }
  input[type=range] { width: 100%; touch-action: none; }
  button { font: inherit; padding: 6px 12px; margin: 4px 4px 0 0; border-radius: 6px; border: 1px solid var(--line); background: var(--card); color: var(--fg); }
  button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
  #error { color: var(--warn); min-height: 1.4em; }
</style>
</head>
<body>
<h1>Jack <span class="muted" id="mumble"></span></h1>
<div>Mouth: <button id="mode-live">live voice</button><button id="mode-show">show control</button></div>
<p id="error"></p>
<div id="motors"></div>
<script>
// Sliders resend their value every 50 ms while held: Jack rests a motor 0.5 s after values stop (dead-man).
const RESEND_MS = 50;
const motorsEl = document.getElementById("motors");
const errorEl = document.getElementById("error");
let built = false;

async function post(path, body) {
  try {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
    errorEl.textContent = response.ok ? "" : (await response.json()).error;
  } catch (error) {
    errorEl.textContent = "Jack is unreachable: " + error;
  }
}

function buildMotor(name, info) {
  const card = document.createElement("div");
  card.className = "motor";
  const low = info.two_sided ? -1 : 0;
  card.innerHTML = `<h2><span>${name}${info.calibrated ? "" : ' <span class="muted">(uncalibrated)</span>'}</span>` +
    `<span class="muted" id="${name}-state"></span></h2>` +
    `<input type="range" id="${name}-slider" min="${low}" max="1" step="0.01" value="0" aria-label="${name}">` +
    `<div id="${name}-poses"></div><button id="${name}-rest">rest</button>`;
  motorsEl.appendChild(card);
  const slider = card.querySelector(`#${name}-slider`);
  let timer = null;
  const send = () => post("/" + name, { value: Number(slider.value) });
  const release = () => { if (timer) { clearInterval(timer); timer = null; } slider.value = 0; post("/" + name + "/rest"); };
  slider.addEventListener("pointerdown", () => { send(); timer = setInterval(send, RESEND_MS); });
  slider.addEventListener("input", send);
  slider.addEventListener("pointerup", release);
  slider.addEventListener("pointercancel", release);
  const poses = card.querySelector(`#${name}-poses`);
  for (const pose of info.poses) {
    const button = document.createElement("button");
    button.textContent = pose;
    button.addEventListener("click", () => post("/" + name + "/pose", { name: pose }));
    poses.appendChild(button);
  }
  card.querySelector(`#${name}-rest`).addEventListener("click", () => post("/" + name + "/rest"));
}

function show(status) {
  if (!built) {
    for (const [name, info] of Object.entries(status.motors)) buildMotor(name, info);
    built = true;
  }
  document.getElementById("mumble").textContent = status.mumble_connected ? "· Mumble connected" : "· Mumble disconnected";
  document.getElementById("mode-live").classList.toggle("on", status.mouth_mode === "live");
  document.getElementById("mode-show").classList.toggle("on", status.mouth_mode === "show");
  for (const [name, info] of Object.entries(status.motors)) {
    const state = document.getElementById(name + "-state");
    const command = info.command ? (info.command.pose ? `pose ${info.command.pose}` : `value ${info.command.value}`) : "rest";
    state.textContent = `${command} · ${info.volts.toFixed(2)} V` + (info.max_hold_tripped ? " · MAX HOLD" : "");
    state.classList.toggle("warn", info.max_hold_tripped);
  }
}

async function refresh() {
  try {
    show(await (await fetch("/status")).json());
  } catch (error) {
    errorEl.textContent = "Jack is unreachable: " + error;
  }
}

document.getElementById("mode-live").addEventListener("click", () => post("/mouth/mode", { mode: "live" }).then(refresh));
document.getElementById("mode-show").addEventListener("click", () => post("/mouth/mode", { mode: "show" }).then(refresh));
refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_http_server.py -v && .venv/bin/pytest -q` → pass, pristine (no request log lines).

- [ ] **Step 5: Commit** `motor_test/http_server.py motor_test/control_page.html tests/test_http_server.py` — "Serve HTTP show control with /status and a control page"

---

### Task 11: Compose show control in `main.py` and `lipsync_wav.py`

**Files:**
- Modify: `main.py`, `lipsync_wav.py`, `motor_test/mumble_voice.py`
- Test: `tests/test_main.py`, `tests/test_mumble_voice.py`, `tests/test_lipsync_wav.py` (only if a test referenced removed names)

**Interfaces:**
- Consumes: everything above.
- Produces: `main.ShowControlConfig(osc_port: int, http_port: int, timeout_s: float, mouth_mode: str)`, `main.show_control_config(environ=os.environ) -> ShowControlConfig`, `main.build_motors(bus) -> dict[str, Tb6612Motor]`; `MumbleVoice.connected: bool`.

- [ ] **Step 1: Failing tests.** Append to `tests/test_mumble_voice.py`:

```python
def test_connected_follows_the_connection_callbacks():
    source, _ = voice()
    assert source.connected is False
    source.on_connected()
    assert source.connected is True
    source.on_disconnected()
    assert source.connected is False
```

Append to `tests/test_main.py`:

```python
from tests.fakes import RecordingBus


def test_show_control_defaults():
    assert main.show_control_config({}) == main.ShowControlConfig(osc_port=9000, http_port=8080, timeout_s=0.5, mouth_mode="live")


def test_show_control_from_the_environment():
    config = main.show_control_config(
        {"JACK_OSC_PORT": "9100", "JACK_HTTP_PORT": "8181", "JACK_CONTROL_TIMEOUT_S": "1.5", "JACK_MOUTH_MODE": "show"}
    )
    assert config == main.ShowControlConfig(osc_port=9100, http_port=8181, timeout_s=1.5, mouth_mode="show")


@pytest.mark.parametrize(
    "var, value",
    [("JACK_OSC_PORT", "x"), ("JACK_OSC_PORT", "70000"), ("JACK_HTTP_PORT", "0"), ("JACK_CONTROL_TIMEOUT_S", "nan"),
     ("JACK_CONTROL_TIMEOUT_S", "-1"), ("JACK_MOUTH_MODE", "auto")],
)
def test_bad_show_control_settings_exit_naming_the_variable(var, value):
    with pytest.raises(SystemExit) as exit_info:
        main.show_control_config({var: value})
    assert var in str(exit_info.value.code)


def test_build_motors_puts_each_motor_on_its_hat_and_channel():
    bus = RecordingBus()
    motors = main.build_motors(bus)
    assert set(motors) == {"mouth", "hand", "pivot", "elbow"}
    motors["elbow"].drive(100)
    assert (0x41, 0x06 + 4 * 5 + 2, 100) in bus.writes  # HAT 2, channel B's PWM (channel 5) OFF_L


class MissingHatBus(RecordingBus):
    def write_byte_data(self, i2c_addr, register, value):
        if i2c_addr == 0x41:
            raise OSError("[Errno 121] Remote I/O error")
        super().write_byte_data(i2c_addr, register, value)


def test_a_missing_hat_exits_naming_its_address():
    with pytest.raises(SystemExit) as exit_info:
        main.build_motors(MissingHatBus())
    assert "0x41" in str(exit_info.value.code)
```

(Check `RecordingBus.writes` records `(i2c_addr, register, value)` — it does — and that `Pca9685.set_off_count` writes the OFF_L byte at `0x06 + 4*channel + 2`; adjust only the register arithmetic if the chip writes differently, keeping the assertion that the write went to `0x41`.)

Run → failures (`AttributeError`).

- [ ] **Step 2: `motor_test/mumble_voice.py`** — in `__init__` add `self.connected = False`; `on_connected` sets `self.connected = True` before logging; `on_disconnected` sets it `False` before logging.

- [ ] **Step 3: `main.py`**

Imports to add: `import time`, `from dataclasses import dataclass`, `from motor_test.control_board import MOUTH_MODES, ControlBoard`, `from motor_test.http_server import start_http_server`, `from motor_test.motors import MOTORS`, `from motor_test.osc_server import start_osc_server`, `from motor_test.rate_limited_log import RateLimitedLog`, `from motor_test.show_commands import handle_osc`, `from motor_test.tb6612_motor import MOTOR_CHANNELS` (keep `MOTOR_A`/`MOTOR_B` only if still used). Remove `PCA9685_ADDRESS` (addresses now come from `motors.MOTORS`); update its importers (`lipsync_wav.py`; `calibrate.py` stops using it in Task 12 — if Task 12 already landed, it doesn't import it).

Add:

```python
# Show control (SPEC.md "Show control"); every value can be set in /etc/jack/jack.env.
DEFAULT_OSC_PORT = 9000
DEFAULT_HTTP_PORT = 8080
DEFAULT_CONTROL_TIMEOUT_S = 0.5
# One log line per kind of bad OSC message per minute.
OSC_LOG_INTERVAL_S = 60.0


@dataclass(frozen=True)
class ShowControlConfig:
    osc_port: int
    http_port: int
    timeout_s: float
    mouth_mode: str


def show_control_config(environ: Mapping[str, str] = os.environ) -> ShowControlConfig:
    """Ports, dead-man timeout and startup mouth mode, from jack.env or their defaults."""
    mode = environ.get("JACK_MOUTH_MODE") or "live"
    if mode not in MOUTH_MODES:
        raise SystemExit(f"JACK_MOUTH_MODE={mode!r} in /etc/jack/jack.env must be one of {', '.join(MOUTH_MODES)}")
    timeout = _env_number(environ, "JACK_CONTROL_TIMEOUT_S", DEFAULT_CONTROL_TIMEOUT_S)
    if timeout <= 0:
        raise SystemExit(f"JACK_CONTROL_TIMEOUT_S={timeout!r} in /etc/jack/jack.env must be positive")
    return ShowControlConfig(
        osc_port=_env_port(environ, "JACK_OSC_PORT", DEFAULT_OSC_PORT),
        http_port=_env_port(environ, "JACK_HTTP_PORT", DEFAULT_HTTP_PORT),
        timeout_s=timeout,
        mouth_mode=mode,
    )


def _env_number(environ: Mapping[str, str], var: str, default: float) -> float:
    value = environ.get(var, "")
    if not value:
        return default
    try:
        number = float(value)
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise SystemExit(f"{var}={value!r} in /etc/jack/jack.env is not a number")
    return number


def _env_port(environ: Mapping[str, str], var: str, default: int) -> int:
    value = environ.get(var, "")
    if not value:
        return default
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise SystemExit(f"{var}={value!r} in /etc/jack/jack.env must be a port number 1-65535")
    return int(value)


def build_motors(bus) -> dict[str, Tb6612Motor]:
    """Every motor on its HAT and channel; a HAT that doesn't answer stops the app naming its address."""
    chips: dict[int, Pca9685] = {}
    for spec in MOTORS:
        if spec.address not in chips:
            try:
                chips[spec.address] = Pca9685(bus, spec.address, PWM_FREQ_HZ)
            except OSError as error:
                raise SystemExit(
                    f"Motor HAT at {spec.address:#04x} is not responding ({error}); check it is seated and its address pads"
                ) from error
    return {spec.name: Tb6612Motor(chips[spec.address], MOTOR_CHANNELS[spec.channel]) for spec in MOTORS}
```

Replace `main()`:

```python
def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    settings = talk_settings()
    profiles = motor_profiles()
    config = show_control_config()
    if overrides := describe_overrides(settings):
        print(f"Mouth setting overrides: {overrides}")
    board = ControlBoard(profiles, config.timeout_s, config.mouth_mode, time.monotonic)
    voice = MumbleVoice(settings.max_backlog_frames, print)
    mumble_client = connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, mumble_password())
    with SMBus(I2C_BUS) as bus:
        motors = build_motors(bus)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        osc_log = RateLimitedLog(print, OSC_LOG_INTERVAL_S, time.monotonic)
        start_osc_server("0.0.0.0", config.osc_port, lambda address, args: handle_osc(address, args, profiles, board, osc_log))
        start_http_server("0.0.0.0", config.http_port, board, profiles, lambda: voice.connected)
        uncalibrated = [name for name, profile in profiles.items() if not profile.calibrated]
        print(
            f"Talking: Mumble voice on {ALSA_DEVICE}; motors {', '.join(motors)} on {SUPPLY_VOLTS} V; "
            f"mouth {config.mouth_mode}; OSC UDP {config.osc_port}, HTTP {config.http_port}"
            + (f"; uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
        )
        notify("READY=1")
        run_talk_loop(
            [voice], sink, motors, profiles, settings, board, SUPPLY_VOLTS, watchdog_while_connected(mumble_client)
        )
```

Update the module docstring: "Jack talks and takes show control: Boss's voice from Mumble plays on the 3.5 mm jack; the mouth follows it (live) or show commands (show); hand, pivot and elbow follow OSC/HTTP show commands."

- [ ] **Step 4: `lipsync_wav.py`** — replace its interim motor/board setup with `motors = build_motors(bus)` (import `build_motors` from `main`; drop `PCA9685_ADDRESS`, `Pca9685`, `Tb6612Motor`, `MOTOR_A`, `MOTOR_B` imports if unused) and `ControlBoard(profiles, DEFAULT_CONTROL_TIMEOUT_S, "live", time.monotonic)` (import `DEFAULT_CONTROL_TIMEOUT_S` from `main`). With no show commands, the hand, pivot and elbow simply rest.

- [ ] **Step 5: Run** `.venv/bin/pytest -q && .venv/bin/python -c "import main, lipsync_wav, calibrate"` → all pass.

- [ ] **Step 6: Commit** `main.py lipsync_wav.py motor_test/mumble_voice.py tests/test_main.py tests/test_mumble_voice.py` (+ `tests/test_lipsync_wav.py` if changed) — "Run show control in Jack: both HATs, OSC and HTTP servers, mouth mode from jack.env"

---

### Task 12: `calibrate.py` for any motor

**Files:**
- Modify: `motor_test/calibration.py`, `calibrate.py`
- Test: `tests/test_calibration.py`, new `tests/test_calibrate.py`

**Interfaces:**
- Consumes: `MotorProfile`, `load_profiles` (Task 3); `MOTOR_NAMES`, `motor_spec` (Task 2); `MOTOR_CHANNELS` (Task 2); `main.POSES_PATHS` (Task 4).
- Produces: `calibration.parse_command(line, supply_volts, profile, step_s) -> tuple[Segment, ...] | None`; `calibration.pose_segments(pose, seconds, slew_v_per_s, step_s) -> tuple[Segment, ...]`; `calibration.run_calibration(motor, motor_name, profile, supply_volts, step_s, read_line, write, sleep)`; `calibrate.main(argv=None) -> int`.

- [ ] **Step 1: Update `tests/test_calibration.py` first.** Imports: drop `CLOSE, RELAX, open_fully` from `motor_test.mouth` (keep `hold`, add `ramp`); add `from tests.profiles import MOUTH, profile` and `from motor_test.calibration import pose_segments`. `run()` becomes:

```python
def run(motor, *lines, then=EOFError, motor_profile=MOUTH):
    output, slept = [], []
    run_calibration(motor, "mouth", motor_profile, SUPPLY, STEP_S, scripted_input(*lines, then=then), output.append, slept.append)
    return output, slept
```

Every `parse_command(line, SUPPLY)` becomes `parse_command(line, SUPPLY, MOUTH, STEP_S)`. Replace `test_close_relax_and_open_name_the_calibrated_poses` and `test_open_ramps_up_before_holding` with:

```python
def test_a_pose_name_ramps_at_the_motors_slew_then_holds_for_its_default_seconds():
    # mouth open: -6 V at 48 V/s = 0.125 s, rounded to whole 0.05 s steps = 0.1 s.
    assert parse_command("open", SUPPLY, MOUTH, STEP_S) == (ramp(0.0, -6.0, 0.1), hold(-6.0, 0.5))


def test_a_pose_name_with_seconds_holds_that_long():
    assert parse_command("relax 0.8", SUPPLY, MOUTH, STEP_S) == (ramp(0.0, -2.0, 0.05), hold(-2.0, 0.8))


def test_pose_segments_ramp_at_least_one_step():
    assert pose_segments(MOUTH.poses["close"], 0.25, 48.0, STEP_S) == (ramp(0.0, 1.0, 0.05), hold(1.0, 0.25))


def test_poses_come_from_the_motors_profile():
    elbow = profile("elbow")
    assert parse_command("up", SUPPLY, elbow, STEP_S)[-1] == hold(2.0, 0.5)
    with pytest.raises(ValueError, match="up"):
        parse_command("open", SUPPLY, elbow, STEP_S)
```

Any other test expecting `CLOSE`/`RELAX` segments or the `"moved B …"` message: update to the pose-segment form and `"moved mouth …"`. Keep every safety test (limits, braking on every exit, rejected lines never move).

`tests/test_calibrate.py`:

```python
import pytest

import calibrate


def test_refuses_while_the_app_is_running(monkeypatch, capsys):
    monkeypatch.setattr(calibrate, "app_is_running", lambda: True)
    assert calibrate.main(["elbow"]) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_unknown_motor_is_a_usage_error():
    with pytest.raises(SystemExit) as exit_info:
        calibrate.main(["tail"])
    assert exit_info.value.code == 2


def test_invalid_poses_file_is_reported_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(calibrate, "app_is_running", lambda: False)
    bad = tmp_path / "poses.toml"
    bad.write_text("[elbow]\nmax_v = 99\n")
    monkeypatch.setattr(calibrate, "POSES_PATHS", (calibrate.POSES_PATHS[0], bad))
    assert calibrate.main(["elbow"]) == 2
    assert "99" in capsys.readouterr().err
```

Run → failures for the expected reasons.

- [ ] **Step 2: `motor_test/calibration.py`**

```python
"""Interactive calibration: drive one motor at a typed voltage, or one of its poses, then brake."""

from collections.abc import Callable

from motor_test.mouth import Segment, describe, hold, ramp
from motor_test.ports import MotorOutput
from motor_test.poses import MotorProfile, Pose
from motor_test.ramp import segment_profile

# Longest single move, so a typo can't hold the motor stalled against an end stop for long.
MAX_MOVE_S = 3.0

USAGE = "enter '<volts> <seconds>' (e.g. '-4.5 0.8'), '<pose>' or '<pose> <seconds>', or 'q' to quit"


def parse_command(line: str, supply_volts: float, profile: MotorProfile, step_s: float) -> tuple[Segment, ...] | None:
    """Parse a command into the segments it plays, or return None for 'q'/'quit'.

    Raises ValueError, with a message fit to show the operator, for anything else.
    """
    words = line.split()
    if words in (["q"], ["quit"]):
        return None
    if words and words[0] in profile.poses:
        if len(words) > 2:
            raise ValueError(_usage(profile))
        pose = profile.poses[words[0]]
        seconds = _seconds(words[1]) if len(words) == 2 else _seconds(str(pose.seconds))
        return pose_segments(pose, seconds, profile.slew_v_per_s, step_s)
    if len(words) != 2:
        raise ValueError(_usage(profile))
    try:
        volts = float(words[0])
    except ValueError:
        raise ValueError(_usage(profile)) from None
    if not -supply_volts <= volts <= supply_volts:
        raise ValueError(f"volts must be between -{supply_volts} and {supply_volts}, got {words[0]}")
    return (hold(volts, _seconds(words[1])),)


def pose_segments(pose: Pose, seconds: float, slew_v_per_s: float, step_s: float) -> tuple[Segment, ...]:
    """Ramp from 0 V to the pose's volts at the motor's slew limit (whole steps, at least one), then hold."""
    ramp_steps = max(1, round(abs(pose.volts) / slew_v_per_s / step_s))
    return (ramp(0.0, pose.volts, ramp_steps * step_s), hold(pose.volts, seconds))


def _usage(profile: MotorProfile) -> str:
    return f"{USAGE}; poses: {', '.join(sorted(profile.poses))}"


def _seconds(word: str) -> float:
    try:
        seconds = float(word)
    except ValueError:
        raise ValueError(USAGE) from None
    if not 0 < seconds <= MAX_MOVE_S:
        raise ValueError(f"seconds must be more than 0 and at most {MAX_MOVE_S}, got {word}")
    return seconds


def run_calibration(
    motor: MotorOutput,
    motor_name: str,
    profile: MotorProfile,
    supply_volts: float,
    step_s: float,
    read_line: Callable[[], str],
    write: Callable[[str], None],
    sleep: Callable[[float], None],
) -> None:
    """Perform typed moves until 'q' or end of input (EOFError from `read_line`).

    Each command plays step by step, one duty count per `step_s`, so ramps are
    smooth and durations must be whole steps.

    The motor is braked after every move and on every way out, including
    KeyboardInterrupt and I2C errors, which are re-raised after braking.
    """
    try:
        while True:
            try:
                line = read_line()
            except EOFError:
                return
            if not line.strip():
                continue
            try:
                segments = parse_command(line, supply_volts, profile, step_s)
                if segments is None:
                    return
                counts = segment_profile(segments, supply_volts, step_s)
            except ValueError as problem:
                write(str(problem))
                continue
            try:
                for count in counts:
                    motor.drive(count)
                    sleep(step_s)
            finally:
                motor.stop()
            write(f"moved {motor_name} {describe(segments)}, braked")
    finally:
        motor.stop()
```

(`mouth.ramp` exists: `ramp(start_volts, end_volts, seconds)`.)

- [ ] **Step 3: `calibrate.py`**

```python
"""Interactive calibration for one of Jack's motors: type '<volts> <seconds>' or a pose name; it moves, then brakes.

Stop the app first so the two programs don't fight over the HATs:
    sudo systemctl stop jack
    /opt/jack-venv/bin/python /opt/jack/calibrate.py elbow
"""

import argparse
import sys
import time

from smbus2 import SMBus

from main import I2C_BUS, POSES_PATHS, PWM_FREQ_HZ, STEP_S, SUPPLY_VOLTS
from motor_test.calibration import MAX_MOVE_S, USAGE, run_calibration
from motor_test.motors import MOTOR_NAMES, motor_spec
from motor_test.pca9685 import Pca9685
from motor_test.poses import load_profiles
from motor_test.service_guard import STOP_APP_FIRST, app_is_running
from motor_test.tb6612_motor import MOTOR_CHANNELS, Tb6612Motor


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
        print(error, file=sys.stderr)
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
```

- [ ] **Step 4: Run** `.venv/bin/pytest tests/test_calibration.py tests/test_calibrate.py -v && .venv/bin/pytest -q` → all pass.

- [ ] **Step 5: Commit** `motor_test/calibration.py calibrate.py tests/test_calibration.py tests/test_calibrate.py` — "Calibrate any motor by name, with its poses from poses.toml"

---

### Task 13: Docs

**Files:**
- Modify: `README.md`, `SPEC.md`

- [ ] **Step 1: README.md** — add a "Show control" section after "Talking": what it is; OSC table (copy the spec's address table); HTTP routes and `curl` examples (`curl -X POST http://10.10.0.54:8080/hand -d '{"value": 0.5}'`, `curl http://10.10.0.54:8080/status`); the control page at `http://10.10.0.54:8080/` (and the macOS Local Network permission note); dead-man rule in one sentence (keep sending values; 0.5 s of silence rests the motor); mouth mode (`/jack/mouth/mode show`, `JACK_MOUTH_MODE`); `jack.env` variables (`JACK_OSC_PORT`, `JACK_HTTP_PORT`, `JACK_CONTROL_TIMEOUT_S`, `JACK_MOUTH_MODE`); `/etc/jack/poses.toml` overrides with a 3-line example; calibrating a new motor: `sudo systemctl stop jack` then `/opt/jack-venv/bin/python /opt/jack/calibrate.py elbow`. Update the "Tuning the lip sync" section: the mouth's volts (`min_v`, `max_v`, `slew_v_per_s`, `rest_pulse_v`, `rest_pulse_s`) are tuned in `/etc/jack/poses.toml` `[mouth]`, not `jack.env`. Update the calibration section to `calibrate.py <motor>`.

- [ ] **Step 2: SPEC.md** — "Calibration tool" section: usage is now `calibrate.py <motor>`, poses from `poses.toml` ramp at the motor's slew. "Code structure": add `motor_driver.py`, `rate_limited_log.py`; `main.py` bullet: builds both HATs (`build_motors`), loads profiles, starts OSC/HTTP, mouth mode from `show_control_config`. "Testing" and "Deployment": `python-osc==1.10.2` in both requirements files. Check nothing else in SPEC.md still claims the old single-motor behavior of `main.py` or `calibrate.py`.

- [ ] **Step 3: Commit** `README.md SPEC.md` — "Docs: show control, poses.toml, calibrate any motor"

---

### Task 14: Roll out on the Pi (with Boss)

- [ ] **Step 1:** Boss's go-ahead to push. Before pushing, check `/etc/jack/jack.env` with Boss for any of `JACK_OPEN_MIN_V`, `JACK_OPEN_MAX_V`, `JACK_OPEN_SLEW_V_PER_S`, `JACK_CLOSE_V`, `JACK_CLOSE_S` (they now stop the app; move them to `/etc/jack/poses.toml` `[mouth]` as `min_v`, `max_v`, `slew_v_per_s`, `rest_pulse_v`, `rest_pulse_s`).
- [ ] **Step 2:** Push. The updater installs `python-osc==1.10.2` (changed `requirements-pi.txt`) before switching code, then restarts Jack. Confirm in `journalctl -u jack -u jack-update`: the pip install, then `Talking: … motors mouth, hand, pivot, elbow … OSC UDP 9000, HTTP 8080; uncalibrated: hand, pivot, elbow`, then `Connected to Mumble`.
- [ ] **Step 3:** From the Mac: `curl http://10.10.0.54:8080/status`; open `http://10.10.0.54:8080/` in a browser (grant Local Network permission if asked); send one OSC message from Boss's show controller (or `python -c "from pythonosc.udp_client import SimpleUDPClient as C; C('10.10.0.54', 9000).send_message('/jack/rest', [])"`). Talk on Mumble: live mouth unchanged.
- [ ] **Step 4:** When Boss wires each new motor: `sudo systemctl stop jack`, `calibrate.py <motor>` to find direction, working volts and safe hold; write the values into `/etc/jack/poses.toml` (and later into the repo's `poses.toml` with `calibrated = true`); `sudo systemctl start jack`; try the poses and sliders.
- [ ] **Step 5:** Recovery checks with four motors: `kill -9`, `kill -STOP`, reboot, `systemctl restart mumble-server` — Jack comes back each time; all motors rest while it's down.
- [ ] **Step 6:** Record results (board check readings if not yet, per-motor calibration, any brake/coast choice) in SPEC.md; commit; push only if Boss asks.
