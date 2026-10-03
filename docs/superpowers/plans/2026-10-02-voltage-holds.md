# Voltage-Dependent Holds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace each motor's single `max_hold_s` stall cutoff with measured `holds` points, so the stall limit depends on the voltage actually driven.

**Architecture:** `motor_test/poses.py` gains a `Hold` point type, `MotorProfile.holds`, and `MotorProfile.hold_s(volts)` (per-direction linear interpolation that raises `ValueError` for an unmeasured voltage); the loader parses `holds` and refuses any config that could drive on an unmeasured hold. `motor_test/motor_driver.py` swaps its tick counter for a stall budget: each driven tick spends `TICK_S / hold_s(volts)`, any rest refills it. Three tasks, each leaving the suite green: add holds alongside `max_hold_s`, switch the driver, then remove and refuse `max_hold_s`.

**Tech Stack:** Python 3.13, `tomllib`, pytest. Run tests with `.venv/bin/python -m pytest -q -W error` from the repo root (`/Users/robbiebyrd/jack`).

**Spec:** `SPEC.md`, sections "Poses and per-motor settings" and "Command board" (commit 3a508f5).

## Global Constraints

- Work directly on `main`; no branches or worktrees. Other sessions may share the checkout: check `git status` before committing and stage only the files the task names (never `git add -A`; `.DS_Store` is not ours).
- Do not push. Pushing deploys to the Pi.
- TDD: write the failing test, run it and see it fail, then implement.
- Test output must be pristine: the full suite passes with `-W error`.
- Match the surrounding style: line length ≤ 120, docstrings on public functions, comments say what/why (never history).
- Hold semantics (SPEC): "hold" is the safe total time driven at a voltage. The hold at V is read only from points with V's sign: interpolated linearly on |V| between points, and the lowest point's hold below the lowest point.
- Refusals at load (SPEC): a hold point's seconds not positive or its volts 0; no hold point at or above the magnitude, in its direction, of the continuous range (`sign × max_v`, and `−sign × max_v` for a two-sided motor), any pose, or the rest pulse; a pose longer than the hold at its volts; the rest pulse longer than the hold at its volts; a leftover `max_hold_s` (message says to write `holds`).
- Budget (SPEC): each driven tick at the volts actually applied (after slew) uses `TICK_S / hold(volts)`; when spent, the motor is forced to rest until a rest command arrives or commands stop; any rest refills at once; rest-pulse ticks use none.
- Repo values (SPEC): mouth holds +2 V 2 s, +6 V 0.5 s, −0.25 V 1 s, −5 V 0.25 s. Hand holds +6 V 2 s, +5 V 2 s, +3 V 5 s, −1 V 4 s, −3 V 1 s, −5 V 0.5 s; hand `max_v` 5; hand `curl` +6 V 1.5 s, `open` −5 V 0.5 s. Pivot holds ±6 V 4 s, ±7 V 4 s. Elbow (placeholder) holds ±2 V 1 s.

## Review Focus

- Two hold points with the same volts would make interpolation ambiguous: the loader must refuse duplicates, naming the volts (test in Task 1).
- An override file's `holds` replaces the base file's whole list (like every non-pose key), so an override that lists only positive points leaves a two-sided motor unmeasured on its negative side: it must be refused, not silently accepted (test in Task 1).
- A one-sided motor with no negative hold points and no negative pose or rest pulse must load fine; the negative side is only checked where something drives it (test in Task 1).
- A reversal that slews through exactly 0 V must neither crash `hold_s` nor spend budget (test in Task 2).
- A pose sent with a longer `seconds` than its hold (allowed at command time) must still be stopped by the budget at the hold, not run to its requested length (test in Task 2).

---

### Task 1: Hold points in profiles and the loader (alongside `max_hold_s`)

**Files:**
- Modify: `motor_test/poses.py`
- Modify: `poses.toml`
- Modify: `tests/profiles.py`
- Test: `tests/test_poses.py`

**Interfaces:**
- Consumes: `motor_test.motors.motor_spec(name).two_sided` (exists).
- Produces:
  - `motor_test.poses.Hold` — `@dataclass(frozen=True) class Hold: volts: float; seconds: float`
  - `MotorProfile.holds: tuple[Hold, ...]` (new field, placed after `max_hold_s`)
  - `MotorProfile.hold_s(volts: float) -> float` — seconds; raises `ValueError` whose message contains `"no hold measured"` when `volts` is 0 or beyond every point on its side.
  - `poses.toml` key `holds = [ { volts = <V>, seconds = <s> }, ... ]`, required for every motor.

- [ ] **Step 1: Give the test profiles hold points**

In `tests/profiles.py`, import `Hold` and add `holds` to both `MotorProfile(...)` calls. One second at every voltage the tests use keeps the existing 50-tick expectations true once Task 2 lands:

```python
from motor_test.poses import Hold, MotorProfile, Pose

MOUTH = MotorProfile(
    calibrated=True, min_v=1.0, max_v=6.0, sign=-1, slew_v_per_s=48.0, max_hold_s=1.0,
    holds=(Hold(-6.0, 1.0), Hold(1.0, 1.0)),
    rest="brake", rest_pulse_v=0.5, rest_pulse_s=0.08,
    poses={"close": Pose(1.0, 0.25), "relax": Pose(-2.0, 0.5), "open": Pose(-6.0, 0.5)},
)
HAND = MotorProfile(
    calibrated=False, min_v=1.0, max_v=2.0, sign=1, slew_v_per_s=24.0, max_hold_s=1.0,
    holds=(Hold(-2.0, 1.0), Hold(2.0, 1.0)),
    rest="brake", rest_pulse_v=0.0, rest_pulse_s=0.0, poses={"curl": Pose(2.0, 0.5)},
)
```

- [ ] **Step 2: Write the failing `hold_s` tests**

Append to `tests/test_poses.py` (add `Hold` to the `from motor_test.poses import ...` line):

```python
MEASURED = profile(
    "hand", holds=(Hold(2.0, 2.0), Hold(6.0, 0.5), Hold(-0.25, 1.0), Hold(-5.0, 0.25)),
)


def test_hold_at_a_measured_point_is_that_points_seconds():
    assert MEASURED.hold_s(6.0) == 0.5
    assert MEASURED.hold_s(-5.0) == 0.25


def test_hold_between_points_is_interpolated_on_magnitude():
    assert MEASURED.hold_s(4.0) == pytest.approx(1.25)  # halfway from 2 s at 2 V to 0.5 s at 6 V


def test_hold_below_the_lowest_point_is_the_lowest_points_hold():
    assert MEASURED.hold_s(1.0) == 2.0
    assert MEASURED.hold_s(-0.1) == 1.0


def test_hold_is_read_only_from_points_on_the_same_side():
    assert MEASURED.hold_s(-1.0) == pytest.approx(1.0 - 0.75 * 0.75 / 4.75)


@pytest.mark.parametrize("volts", [6.5, -5.5, 0.0])
def test_unmeasured_volts_have_no_hold(volts):
    with pytest.raises(ValueError, match="no hold measured"):
        MEASURED.hold_s(volts)


def test_a_side_with_no_points_has_no_hold():
    with pytest.raises(ValueError, match="no hold measured"):
        profile("hand", holds=(Hold(2.0, 1.0),)).hold_s(-1.0)
```

- [ ] **Step 3: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_poses.py`
Expected: collection error or failures (`Hold` can't be imported / `holds` is not a field).

- [ ] **Step 4: Implement `Hold`, the field and `hold_s`**

In `motor_test/poses.py`: add `import itertools`; add after `Pose`:

```python
@dataclass(frozen=True)
class Hold:
    """A measured safe drive time: driving at `volts` for longer than `seconds` risks the motor."""

    volts: float
    seconds: float
```

Add `holds: tuple[Hold, ...]` to `MotorProfile` right after `max_hold_s: float`, and this method after `volts_for`:

```python
    def hold_s(self, volts: float) -> float:
        """Safe drive time at `volts`, from the hold points on volts' side of 0.

        Interpolated linearly on |volts| between points; below the lowest point, that point's hold.
        Raises ValueError for 0 V or for volts beyond every point on their side (an unmeasured hold).
        """
        side = sorted((abs(hold.volts), hold.seconds) for hold in self.holds if (hold.volts > 0) == (volts > 0))
        magnitude = abs(volts)
        if volts == 0 or not side or magnitude > side[-1][0]:
            raise ValueError(f"no hold measured at or above {volts:+g} V")
        if magnitude <= side[0][0]:
            return side[0][1]
        for (low_v, low_s), (high_v, high_s) in itertools.pairwise(side):
            if magnitude <= high_v:
                return low_s + (high_s - low_s) * (magnitude - low_v) / (high_v - low_v)
        raise AssertionError("unreachable: magnitude is within the side's range")
```

- [ ] **Step 5: Run the `hold_s` tests**

Run: `.venv/bin/python -m pytest -q -W error tests/test_poses.py -k hold`
Expected: the six new tests PASS. (Loader tests still fail until Step 9: `_profile` doesn't pass `holds` yet.)

- [ ] **Step 6: Write the failing loader tests**

In `tests/test_poses.py`, add a `holds` line to `MOTOR_TABLE` (between `max_hold_s` and `rest`). ±3 V lets the existing override test raise `max_v` and add a 3 V pose:

```python
MOTOR_TABLE = """
[{name}]
calibrated = false
min_v = 1.0
max_v = 2.0
sign = 1
slew_v_per_s = 24.0
max_hold_s = 1.0
holds = [{{ volts = 3.0, seconds = 1.0 }}, {{ volts = -3.0, seconds = 1.0 }}]
rest = "brake"
[{name}.poses]
go = {{ volts = 2.0, seconds = 0.5 }}
"""
```

Add these cases to the `test_invalid_entries_are_rejected_naming_what_is_wrong` parametrize list (the override is applied on top of `all_motors()`; pivot is two-sided, hand is two-sided, elbow and mouth are one-sided):

```python
        ("[hand]\nholds = 3\n", "holds"),
        ("[hand]\nholds = []\n", "holds"),
        ("[hand]\nholds = [{ volts = 2.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 0.0, seconds = 1.0 }, { volts = -3.0, seconds = 1.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 3.0, seconds = 0 }, { volts = -3.0, seconds = 1.0 }]\n", "holds"),
        ("[hand]\nholds = [{ volts = 20.0, seconds = 1.0 }, { volts = -3.0, seconds = 1.0 }]\n", "supply"),
        (
            "[hand]\nholds = [{ volts = 3.0, seconds = 1.0 }, { volts = 3.0, seconds = 2.0 },"
            " { volts = -3.0, seconds = 1.0 }]\n",
            "more than once",
        ),
        ("[elbow]\nmax_v = 4.0\n", "max_v"),
        ("[pivot]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "max_v"),
        ("[hand.poses]\ncurl = { volts = -4.0, seconds = 0.5 }\n", "curl"),
        ("[hand.poses]\ncurl = { volts = 2.0, seconds = 1.5 }\n", "curl"),
        ("[mouth]\nrest_pulse_v = 4.0\nrest_pulse_s = 0.08\n", "rest_pulse"),
        ("[mouth]\nrest_pulse_v = 0.5\nrest_pulse_s = 2.0\n", "rest_pulse"),
```

And these tests:

```python
def test_holds_load_as_points(tmp_path):
    base = write(tmp_path, all_motors())
    assert load_profiles([base], SUPPLY)["hand"].holds == (Hold(-3.0, 1.0), Hold(3.0, 1.0))


def test_an_override_replaces_the_whole_holds_list(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[elbow]\nholds = [{ volts = 3.0, seconds = 0.5 }]\n", "pi.toml")
    assert load_profiles([base, override], SUPPLY)["elbow"].holds == (Hold(3.0, 0.5),)


def test_a_one_sided_motor_needs_no_points_on_a_side_nothing_drives(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[elbow]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "pi.toml")
    assert load_profiles([base, override], SUPPLY)["elbow"].hold_s(2.0) == 1.0


def test_an_unmeasured_hold_names_the_files_that_set_the_motor(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[pivot]\nholds = [{ volts = 3.0, seconds = 1.0 }]\n", "pi.toml")
    with pytest.raises(ValueError, match="no hold measured") as error:
        load_profiles([base, override], SUPPLY)
    assert str(base) in str(error.value) and str(override) in str(error.value)
```

Replace `test_repo_poses_fit_inside_each_motors_max_hold` with:

```python
def test_repo_poses_fit_inside_their_holds():
    for name, motor in load_profiles([REPO_POSES], SUPPLY).items():
        for pose_name, pose in motor.poses.items():
            assert pose.seconds <= motor.hold_s(pose.volts), f"{name} {pose_name}"
```

In `test_repo_poses_file_matches_the_spec`, change the hand line to `max_v` 5 and add `holds` assertions after each motor's first assertion line:

```python
    assert mouth.holds == (Hold(-5.0, 0.25), Hold(-0.25, 1.0), Hold(2.0, 2.0), Hold(6.0, 0.5))
    ...
    assert (hand.calibrated, hand.min_v, hand.max_v, hand.sign, hand.max_hold_s) == (True, 3.0, 5.0, 1, 0.5)
    assert hand.holds == (
        Hold(-5.0, 0.5), Hold(-3.0, 1.0), Hold(-1.0, 4.0), Hold(3.0, 5.0), Hold(5.0, 2.0), Hold(6.0, 2.0)
    )
    ...
    assert pivot.holds == (Hold(-7.0, 4.0), Hold(-6.0, 4.0), Hold(6.0, 4.0), Hold(7.0, 4.0))
    ...
    assert elbow.holds == (Hold(-2.0, 1.0), Hold(2.0, 1.0))
```

(The loader stores holds sorted by volts, which is why the expected tuples are ascending.)

- [ ] **Step 7: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_poses.py`
Expected: FAIL — `holds` is an unknown key / `MotorProfile` missing `holds`.

- [ ] **Step 8: Add `holds` to `poses.toml`**

Add a `holds` line after each motor's `max_hold_s` line, set the hand's `max_v` to 5, and leave every other value as it is:

```toml
# [mouth]
holds = [
  { volts = 2.0, seconds = 2.0 }, { volts = 6.0, seconds = 0.5 },
  { volts = -0.25, seconds = 1.0 }, { volts = -5.0, seconds = 0.25 },
]
```
```toml
# [hand]
max_v = 5.0          # opening holds were measured only to -5 V
holds = [
  { volts = 6.0, seconds = 2.0 }, { volts = 5.0, seconds = 2.0 }, { volts = 3.0, seconds = 5.0 },
  { volts = -1.0, seconds = 4.0 }, { volts = -3.0, seconds = 1.0 }, { volts = -5.0, seconds = 0.5 },
]
```
```toml
# [pivot]
holds = [
  { volts = 6.0, seconds = 4.0 }, { volts = 7.0, seconds = 4.0 },
  { volts = -6.0, seconds = 4.0 }, { volts = -7.0, seconds = 4.0 },
]
```
```toml
# [elbow]
holds = [{ volts = 2.0, seconds = 1.0 }, { volts = -2.0, seconds = 1.0 }]  # placeholder
```

Update the header comment in `poses.toml` with one line: `# holds: measured safe drive times; the hold at a voltage is interpolated from the points on its side of 0.`

- [ ] **Step 9: Parse and validate `holds` in the loader**

In `motor_test/poses.py`:

1. Change the import to `from motor_test.motors import MOTOR_NAMES, motor_spec`.
2. Add `"holds"` to `_REQUIRED` (after `"max_hold_s"`).
3. In `_profile`, after the `max_hold_s` block, parse the points:

```python
    with _in_file(origins, "holds"):
        holds = _holds(where, table["holds"], supply_volts)
```

4. Pass `holds=holds` to `MotorProfile(...)`, then validate the built profile before returning it:

```python
    profile = MotorProfile(
        calibrated=table["calibrated"], min_v=min_v, max_v=max_v, sign=int(table["sign"]), slew_v_per_s=slew,
        max_hold_s=max_hold_s, holds=holds, rest=table["rest"], rest_pulse_v=rest_pulse_v,
        rest_pulse_s=rest_pulse_s, poses=poses,
    )
    with all_files:
        _check_holds(where, profile, motor_spec(name).two_sided)
    return profile
```

5. Add the helpers (next to `_pose`):

```python
def _holds(where: str, value: object, supply_volts: float) -> tuple[Hold, ...]:
    """The hold points, sorted by volts; each nonzero, within the supply, positive seconds, volts unique."""
    if not isinstance(value, list) or not value:
        raise ValueError(f"{where}: holds must be a non-empty list of {{ volts = <V>, seconds = <s> }}, got {value!r}")
    holds = []
    for spec in value:
        if not isinstance(spec, dict) or set(spec) != {"volts", "seconds"}:
            raise ValueError(f"{where}: each of holds must be {{ volts = <V>, seconds = <s> }}, got {spec!r}")
        volts = _number(f"{where} holds", spec, "volts")
        _within_supply(f"{where} holds", "volts", volts, supply_volts)
        seconds = _number(f"{where} holds", spec, "seconds")
        if volts == 0 or seconds <= 0:
            raise ValueError(f"{where}: holds need nonzero volts and positive seconds, got {spec!r}")
        holds.append(Hold(volts, seconds))
    volts_seen = [hold.volts for hold in holds]
    duplicates = sorted({volts for volts in volts_seen if volts_seen.count(volts) > 1})
    if duplicates:
        raise ValueError(f"{where}: holds list {', '.join(f'{volts:+g} V' for volts in duplicates)} more than once")
    return tuple(sorted(holds, key=lambda hold: hold.volts))


def _check_holds(where: str, profile: MotorProfile, two_sided: bool) -> None:
    """Refuse anything that could drive the motor on an unmeasured hold, or longer than its hold."""
    driven = [("max_v", profile.sign * profile.max_v)]
    if two_sided:
        driven.append(("max_v", -profile.sign * profile.max_v))
    for what, volts in driven:
        _hold_at(f"{where} {what}", profile, volts)
    for name, pose in profile.poses.items():
        _fits_hold(f"{where} pose {name}", profile, pose.volts, pose.seconds)
    if profile.rest_pulse_s > 0 and profile.rest_pulse_v != 0:
        _fits_hold(f"{where} rest_pulse", profile, profile.rest_pulse_v, profile.rest_pulse_s)


def _hold_at(where: str, profile: MotorProfile, volts: float) -> float:
    try:
        return profile.hold_s(volts)
    except ValueError as error:
        raise ValueError(f"{where}: drives {volts:+g} V, but {error}; add a holds point") from error


def _fits_hold(where: str, profile: MotorProfile, volts: float, seconds: float) -> None:
    hold = _hold_at(where, profile, volts)
    if seconds > hold:
        raise ValueError(f"{where}: {seconds:g} s is longer than the {hold:g} s hold at {volts:+g} V")
```

- [ ] **Step 10: Run the full suite**

Run: `.venv/bin/python -m pytest -q -W error`
Expected: all PASS. If `test_the_repo_layout_file_is_up_to_date` fails, regenerate with `.venv/bin/python -m tools.touchosc_layout` and rerun (the hand's fader range is unchanged, so it should already pass).

- [ ] **Step 11: Commit**

```bash
git add motor_test/poses.py poses.toml tests/profiles.py tests/test_poses.py
git commit -m "Hold points per motor in poses.toml, refusing any drive on an unmeasured hold"
```

---

### Task 2: The driver spends a stall budget instead of counting ticks

**Files:**
- Modify: `motor_test/motor_driver.py`
- Test: `tests/test_motor_driver.py`

**Interfaces:**
- Consumes: `MotorProfile.hold_s(volts: float) -> float` and `Hold` from Task 1.
- Produces: `MotorDriver` with the same public surface (`update`, `resume_from`, `max_hold_tripped`); it no longer reads `profile.max_hold_s`.

- [ ] **Step 1: Write the failing budget tests**

Append to `tests/test_motor_driver.py` (add `from motor_test.poses import Hold`):

```python
def budget_profile():
    # 6 V lasts 0.4 s (20 ticks, 0.05 a tick); 2 V lasts 2 s (100 ticks, 0.01 a tick).
    return profile("hand", slew_v_per_s=1000.0, max_v=6.0, holds=(Hold(2.0, 2.0), Hold(6.0, 0.4), Hold(-2.0, 1.0)))


def test_a_steady_high_voltage_trips_at_its_hold():
    driver = MotorDriver(budget_profile())
    assert run(driver, [6.0] * 20) == [6.0] * 20 and not driver.max_hold_tripped
    assert driver.update(6.0) == BRAKE and driver.max_hold_tripped


def test_a_steady_low_voltage_runs_for_its_longer_hold():
    driver = MotorDriver(budget_profile())
    assert run(driver, [2.0] * 100) == [2.0] * 100 and not driver.max_hold_tripped
    assert driver.update(2.0) == BRAKE and driver.max_hold_tripped


def test_mixed_voltages_share_one_budget():
    driver = MotorDriver(budget_profile())
    run(driver, [6.0] * 10)  # half the budget
    assert run(driver, [2.0] * 50) == [2.0] * 50 and not driver.max_hold_tripped  # the other half
    assert driver.update(2.0) == BRAKE and driver.max_hold_tripped


def test_any_rest_refills_the_budget():
    driver = MotorDriver(budget_profile())
    run(driver, [6.0] * 19)
    driver.update(None)
    assert run(driver, [6.0] * 20) == [6.0] * 20 and not driver.max_hold_tripped


def test_a_long_pose_is_stopped_at_the_hold_not_its_requested_length():
    driver = MotorDriver(budget_profile())
    assert run(driver, [6.0] * 30).count(6.0) == 20 and driver.max_hold_tripped


def test_slewing_through_zero_spends_no_budget():
    # 1 V per tick; every hold is 1 s, so each tick at nonzero volts spends 0.02 and 50 such ticks spend it all.
    driver = MotorDriver(profile("pivot", slew_v_per_s=50.0))
    assert run(driver, [1.0, -1.0]) == [1.0, 0.0]  # 1 nonzero tick spent; the 0 V tick spends nothing
    held = run(driver, [-2.0] * 49)  # -1 V, then 48 at -2 V: 49 more, 50 in all
    assert held[0] == -1.0 and held[-1] == -2.0 and not driver.max_hold_tripped
    assert driver.update(-2.0) == BRAKE and driver.max_hold_tripped
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_motor_driver.py`
Expected: FAIL — the driver still trips after `max_hold_s` (1 s = 50 ticks) regardless of volts.

- [ ] **Step 3: Implement the budget**

In `motor_test/motor_driver.py`:

- Module docstring line 3–4: replace "driving away from rest longer than max_hold_s forces a rest" with "driving away from rest spends a stall budget (each tick `TICK_S / hold(volts)`), and spending it forces a rest".
- Remove `from motor_test.ramp import whole_steps` if nothing else uses it.
- Add a module constant: `# Float sums of exact tick fractions can land a hair over 1; a whole budget is still within the hold.\nBUDGET_TOLERANCE = 1e-9`
- In `__init__`, replace `self._max_hold_ticks = ...` and `self._held_ticks = 0` with `self._budget_spent = 0.0`.
- In `resume_from` and `_to_rest`, replace `self._held_ticks = 0` with `self._budget_spent = 0.0` (keep `_to_rest`'s comment, reworded: "Refill even at exactly 0 V (a reversal can land there), so the next command gets its whole budget.").
- In `update`, replace the counter lines with:

```python
        self._volts = self._slewed(target)
        if self._volts != 0.0:
            self._budget_spent += TICK_S / self._profile.hold_s(self._volts)
        if self._budget_spent > 1.0 + BUDGET_TOLERANCE:
            self.max_hold_tripped = True
            return self._to_rest()
        return self._volts
```

- [ ] **Step 4: Run the driver tests, then the full suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_motor_driver.py` then `.venv/bin/python -m pytest -q -W error`
Expected: all PASS, including the pre-existing max-hold tests (their profiles hold 1 s at every voltage they use, so 50 ticks still pass and the 51st trips).

- [ ] **Step 5: Commit**

```bash
git add motor_test/motor_driver.py tests/test_motor_driver.py
git commit -m "Motors spend a voltage-dependent stall budget instead of a fixed max hold"
```

---

### Task 3: Remove `max_hold_s`, restore the full curl, and update the docs

**Files:**
- Modify: `motor_test/poses.py`
- Modify: `poses.toml`
- Modify: `tests/profiles.py`, `tests/test_poses.py`
- Modify: `README.md`, `TODO.md`
- Regenerate: `touchosc/jack.tosc`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `MotorProfile` without `max_hold_s`; a `max_hold_s` key in any poses file is refused with a message containing `"holds"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_poses.py`:
- In the parametrize list, replace `("[hand]\nmax_hold_s = 0.33\n", "max_hold_s"),` with `("[hand]\nmax_hold_s = 1.0\n", "holds"),`.
- Remove the `max_hold_s = 1.0` line from `MOTOR_TABLE`.
- In `test_repo_poses_file_matches_the_spec`, drop `max_hold_s` from the four tuple assertions (e.g. `(mouth.calibrated, mouth.min_v, mouth.max_v, mouth.sign) == (True, 1.0, 6.0, 1)`, and for the elbow `(elbow.max_v, elbow.slew_v_per_s, elbow.sign) == (2.0, 24.0, 1)`), and expect the full curl: `assert hand.poses == {"curl": Pose(6.0, 1.5), "open": Pose(-5.0, 0.5)}`.

Add:

```python
def test_a_leftover_max_hold_s_says_to_write_holds(tmp_path):
    base = write(tmp_path, all_motors())
    override = write(tmp_path, "[hand]\nmax_hold_s = 1.0\n", "pi.toml")
    with pytest.raises(ValueError, match="max_hold_s.*holds") as error:
        load_profiles([base, override], SUPPLY)
    assert str(override) in str(error.value)
```

In `tests/profiles.py`, remove `max_hold_s=1.0,` from both `MotorProfile(...)` calls.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_poses.py`
Expected: FAIL — `max_hold_s` is still required, and `MotorProfile` still requires it.

- [ ] **Step 3: Remove `max_hold_s` from the loader and profile**

In `motor_test/poses.py`:
- Remove `"max_hold_s"` from `_REQUIRED`, the `max_hold_s: float` field, the `max_hold_s` parsing block in `_profile`, and `max_hold_s=max_hold_s` from the constructor call. Delete `_ticks` only if nothing else uses it (the `rest_pulse_s` check does; keep it).
- At the top of `_profile`, before the unknown-key check, refuse the old key:

```python
    if "max_hold_s" in table:
        with _in_file(origins, "max_hold_s"):
            raise ValueError(
                f"{where}: max_hold_s is replaced by holds = [{{ volts = <V>, seconds = <s> }}, ...] (SPEC.md)"
            )
```

- [ ] **Step 4: Update `poses.toml`**

- Remove every `max_hold_s` line, moving any useful comment onto the `holds` line (e.g. the mouth's calibration note).
- Hand: `curl = { volts = 6.0, seconds = 1.5 }   # a full curl from open`; keep `open = { volts = -5.0, seconds = 0.5 }` with the comment `# a full open (0.75 s) is over the -5 V hold: two presses`.

- [ ] **Step 5: Run the full suite and regenerate the TouchOSC layout**

Run: `.venv/bin/python -m tools.touchosc_layout` then `.venv/bin/python -m pytest -q -W error`
Expected: all PASS.

- [ ] **Step 6: Update the docs**

- `README.md` "Motor settings: `poses.toml`" (around line 278): change "each motor's volts, slew, max hold, rest behaviour and named poses" to "each motor's volts, slew, measured holds (safe drive time per voltage), rest behaviour and named poses".
- `README.md` line ~218: "(each motor's max hold still caps any hold)" → "(each motor's holds still cap any hold)".
- `TODO.md` line 7: "1 s max hold" → "1 s holds at ±2 V"; line ~39: "Each motor's `max_hold_s` still caps how long any hold lasts." → "Each motor's `holds` still cap how long any hold lasts."; add to step 3 of the elbow checklist: "how long it can safely hold at each voltage you use (these become its `holds`)".
- Confirm nothing else mentions it: `rg -n "max_hold_s" --glob '!docs/**' .` returns only SPEC.md's line about refusing it and the refusal in `poses.py`/tests.

- [ ] **Step 7: Commit**

```bash
git add motor_test/poses.py poses.toml tests/profiles.py tests/test_poses.py README.md TODO.md touchosc/jack.tosc
git commit -m "Holds replace max_hold_s; the hand's curl pose is a full 1.5 s curl again"
```
