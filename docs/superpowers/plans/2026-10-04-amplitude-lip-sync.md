# Amplitude Lip Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In live mode the mouth's voltage follows the audio's amplitude (amplitude × gain, capped), with a short closing pull when it drops below the mouth's minimum, replacing the gate-and-curve state machine.

**Architecture:** `MouthController` in `jack/show/audio/lip_sync.py` keeps its interface (`MouthController(settings, mouth)`, `update(level_db) -> volts`) and is rewritten inside: target = `max_v × 10^(level_db/20) × 10^(mouth_gain_db/20)`, capped; open (slew-limited growth) at or above `min_v`, else the rest pulse then 0 V; the stall budget stays. `TalkSettings` drops the gates, `full_db` and `open_curve`; `mouth_gain_db` defaults to +14 dB. `main.py` and `lipsync_wav.py` refuse the replaced settings and a gain at which silence would open the mouth.

**Tech Stack:** Python 3.13, pytest. Run tests with `.venv/bin/python -m pytest -q -W error` from the repo root (`/Users/robbiebyrd/jack`).

**Spec:** `SPEC.md` → "Talking" → "Mouth control" (commits c8b876d, 3ff17bc).

## Global Constraints

- Work directly on `main`; stage only the files a task names (`commands-to-run.txt` is not ours). Do not push.
- TDD; pristine output under `-W error`. `tests/test_layers.py` must keep passing (`show/` imports only `show/` and `support/`, plus the allowed standard library: `math` is allowed).
- Mapping (spec): `a = 10 ^ (level_db / 20)`; target `= max_v × a × 10 ^ (MOUTH_GAIN_DB / 20)`, capped at `max_v`; open at or above `min_v` with growing magnitude slew-limited by `slew_v_per_s` and falling magnitude immediate; below `min_v` after an opening: `rest_pulse_v` for `rest_pulse_s` (the mouth's rest pulse), then 0 V; a target back at or above `min_v` interrupts the pull and opens at once.
- Stall budget (spec): each open tick spends it; when spent, the closing pull, and closed until the target drops below `min_v`; every close refills it.
- `MOUTH_GAIN_DB` default **+14 dB** (−14 dBFS fully opens the mouth).
- Gain check (spec): refuse a gain at which silence (−90 dBFS, `FLOOR_DB`) maps at or above `min_v`.
- Replaced settings (spec): `JACK_GATE_OPEN_DB`, `JACK_GATE_CLOSE_DB`, `JACK_FULL_DB`, `JACK_OPEN_CURVE` still set → refuse to start with a one-line message saying lip sync now follows the audio's amplitude and to tune `JACK_MOUTH_GAIN_DB` instead.

## Review Focus

- A level exactly at the `min_v` boundary computes to 0.99999… V by floating point and reads as "closed": tests use levels a little above the boundary (`level_for(1.01)`), and the boundary rule (`>= min_v` opens) is pinned with a value safely on each side (Task 1).
- After the stall budget is spent, sound that stays at or above `min_v` must keep the mouth closed (not re-open every tick) until a quiet moment (Task 1).
- A closing pull interrupted by sound must restart the opening from 0 V under the slew limit, not jump to the full target (Task 1).
- The `JACK_MOUTH_GAIN_DB=30` already in the Pi's `jack.env` stays valid (silence maps to ~0.06 V) but opens the mouth fully at −30 dBFS; the README says to retune it (Task 2).
- `lipsync_wav.py --mouth-gain-db 80` must be refused before touching hardware, like other bad settings (Task 2).

---

### Task 1: Amplitude mapping in `MouthController` and `TalkSettings`

**Files:**
- Modify: `jack/show/audio/lip_sync.py`, `jack/show/audio/talk_settings.py`
- Test: `tests/test_lip_sync.py` (rewrite), `tests/test_talk_settings.py`, `tests/test_main.py` (tests that used removed fields), `tests/test_lipsync_wav.py` (tests that used removed fields), `tests/test_talk_loop.py` (one comment)

**Interfaces:**
- Produces:
  - `TalkSettings` fields, in order: `mouth_gain_db: float = 14.0`, `attack_s`, `release_s`, `mouth_lead_ms`, `max_backlog_ms` (no `gate_open_db`, `gate_close_db`, `full_db`, `open_curve`).
  - `jack.show.audio.lip_sync.check_mouth_gain(settings: TalkSettings, mouth: MotorProfile) -> None`, raising `ValueError` containing `"mouth_gain_db"` when silence would map at or above `min_v`.
  - `MouthController(settings, mouth)` (calls `check_mouth_gain`), `update(level_db: float) -> float`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/test_lip_sync.py` with:

```python
import math

import pytest

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.lip_sync import MouthController, check_mouth_gain
from jack.show.audio.pcm import TICK_S
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.poses import Hold
from tests.profiles import MOUTH, profile

CLOSE_TICKS = round(MOUTH.rest_pulse_s / TICK_S)  # the test mouth's rest pulse: +0.5 V for 4 ticks
DEFAULT_GAIN_DB = TalkSettings().mouth_gain_db


def level_for(volts: float, gain_db: float = DEFAULT_GAIN_DB, max_v: float = MOUTH.max_v) -> float:
    """The smoothed level (dBFS) that maps to `volts` at `gain_db`."""
    return 20 * math.log10(volts / max_v) - gain_db


QUIET = level_for(0.5)  # maps below the test mouth's 1 V min_v
FULL = level_for(6.0)


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(**overrides), profile("mouth", slew_v_per_s=1000.0))


def run(controller, levels):
    return [controller.update(level) for level in levels]


def test_silence_keeps_the_mouth_closed():
    assert run(unslewed(), [FLOOR_DB] * 3) == [0.0, 0.0, 0.0]


def test_the_default_gain_opens_the_mouth_fully_at_minus_14_dbfs_and_louder_stays_fully_open():
    assert run(unslewed(), [-14.0, 0.0]) == pytest.approx([-6.0, -6.0])


def test_volts_are_proportional_to_amplitude():
    controller = unslewed()
    assert controller.update(level_for(3.0)) == pytest.approx(-3.0)
    assert controller.update(level_for(1.5)) == pytest.approx(-1.5)
    assert controller.update(level_for(3.0) - 20 * math.log10(2)) == pytest.approx(-1.5)  # half the amplitude


def test_more_gain_opens_the_mouth_wider_for_the_same_sound():
    doubled = DEFAULT_GAIN_DB + 20 * math.log10(2)
    assert unslewed(mouth_gain_db=doubled).update(level_for(1.5)) == pytest.approx(-3.0)


def test_just_above_min_v_opens_and_just_below_does_not():
    assert unslewed().update(level_for(1.01)) == pytest.approx(-1.01)
    assert unslewed().update(level_for(0.99)) == 0.0


def test_dropping_below_min_v_pulls_closed_then_brakes():
    volts = run(unslewed(), [level_for(3.0)] + [QUIET] * (CLOSE_TICKS + 2))
    assert volts == pytest.approx([-3.0] + [0.5] * CLOSE_TICKS + [0.0, 0.0])


def test_quiet_without_an_opening_needs_no_pull():
    assert run(unslewed(), [QUIET, QUIET]) == [0.0, 0.0]


def test_sound_interrupts_the_closing_pull():
    assert run(unslewed(), [level_for(3.0), QUIET, level_for(3.0)]) == pytest.approx([-3.0, 0.5, -3.0])


def test_opening_is_slew_limited_to_48_volts_per_second():
    controller = MouthController(TalkSettings(), MOUTH)
    assert run(controller, [FULL] * 3) == pytest.approx([-0.96, -1.92, -2.88])


def test_an_interrupted_pull_reopens_from_zero_under_the_slew_limit():
    controller = MouthController(TalkSettings(), MOUTH)
    run(controller, [FULL] * 10)
    assert run(controller, [QUIET, FULL]) == pytest.approx([0.5, -0.96])


def test_easing_off_is_not_slew_limited():
    controller = MouthController(TalkSettings(), MOUTH)
    run(controller, [FULL] * 20)
    assert controller.update(level_for(2.0)) == pytest.approx(-2.0)


def budgeted():
    """An unslewed controller whose mouth holds fully open (-6 V) for 0.4 s (20 ticks) and 1 V for 2 s."""
    holds = (Hold(-6.0, 0.4), Hold(-1.0, 2.0), Hold(1.0, 1.0))
    return MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, holds=holds))


def test_a_long_full_open_closes_when_its_hold_is_spent():
    volts = run(budgeted(), [FULL] * (20 + CLOSE_TICKS + 1))
    assert volts == pytest.approx([-6.0] * 20 + [0.5] * CLOSE_TICKS + [0.0])


def test_a_smaller_opening_runs_for_its_longer_hold():
    # At 1.01 V the hold interpolates to 2.0 - 1.6 x 0.01 / 5 = 1.9968 s: each tick spends 0.02 / 1.9968, so 99
    # ticks fit and the 100th (1.0016 of the budget) closes the mouth: 50x longer than at full open.
    volts = run(budgeted(), [level_for(1.01)] * 100)
    assert volts == pytest.approx([-1.01] * 99 + [0.5])


def test_a_spent_mouth_stays_closed_through_sound_until_a_quiet_moment():
    controller = budgeted()
    run(controller, [FULL] * (20 + CLOSE_TICKS))
    assert run(controller, [FULL, level_for(1.5), FULL]) == [0.0, 0.0, 0.0]
    assert controller.update(QUIET) == 0.0
    assert run(controller, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_closing_between_syllables_refills_the_budget():
    controller = budgeted()
    run(controller, [FULL] * 19)
    run(controller, [QUIET] * (CLOSE_TICKS + 1))
    assert run(controller, [FULL] * 20) == pytest.approx([-6.0] * 20)


def test_volts_and_the_pull_come_from_the_mouth_profile():
    mouth = profile("mouth", slew_v_per_s=1000.0, min_v=2.0, max_v=4.0, rest_pulse_v=0.7)
    controller = MouthController(TalkSettings(), mouth)
    assert controller.update(level_for(4.0, max_v=4.0)) == pytest.approx(-4.0)
    assert controller.update(level_for(1.5, max_v=4.0)) == 0.7


def test_a_mouth_without_a_rest_pulse_closes_straight_to_zero():
    controller = MouthController(TalkSettings(), profile("mouth", slew_v_per_s=1000.0, rest_pulse_v=0.0, rest_pulse_s=0.0))
    assert run(controller, [level_for(3.0), QUIET, QUIET]) == pytest.approx([-3.0, 0.0, 0.0])


def test_a_gain_at_which_silence_would_open_the_mouth_is_refused():
    # Silence (-90 dBFS) maps to 6 V x 10^((gain - 90) / 20): at or above the 1 V min_v from about +74.4 dB.
    check_mouth_gain(TalkSettings(mouth_gain_db=74.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        check_mouth_gain(TalkSettings(mouth_gain_db=75.0), MOUTH)
    with pytest.raises(ValueError, match="mouth_gain_db"):
        MouthController(TalkSettings(mouth_gain_db=75.0), MOUTH)
```

In `tests/test_talk_settings.py`:
- `test_starting_values_match_the_spec`: replace the `open_curve` and gate assertions with `assert settings.mouth_gain_db == 14.0` and `assert not any(hasattr(settings, name) for name in ("gate_open_db", "gate_close_db", "full_db", "open_curve"))`; keep the attack/release, lead and backlog assertions.
- `test_settings_are_immutable`: assign `TalkSettings().attack_s = 0.5`.
- `test_impossible_settings_are_rejected`: remove the `open_curve`, `gate_close_db`, `full_db` and `mouth_gain_db` cases.
- Delete `test_a_large_but_safe_mouth_gain_is_accepted` (its rule moves to `check_mouth_gain`, tested above).

In `tests/test_main.py` (same behaviours, fields that still exist):
- `test_env_overrides_set_only_the_named_fields`: `{"JACK_MOUTH_GAIN_DB": "20", "JACK_ATTACK_S": "0.02"}` → `TalkSettings(mouth_gain_db=20.0, attack_s=0.02)`.
- `test_empty_override_keeps_the_default`: `{"JACK_ATTACK_S": ""}`.
- `test_override_that_is_not_a_number_exits_naming_the_variable`: `JACK_ATTACK_S='abc'`, message `"JACK_ATTACK_S='abc' in /etc/jack/jack.env is not a number"`.
- `test_impossible_override_exits_saying_the_settings_are_invalid`: `{"JACK_ATTACK_S": "0"}`.
- `test_applied_overrides_are_listed_for_the_log`: `TalkSettings(mouth_gain_db=20.0, attack_s=0.02)` → `"mouth_gain_db=20.0, attack_s=0.02"`.
- `test_mouth_gain_can_be_set_in_jack_env` stays as it is.

In `tests/test_lipsync_wav.py`:
- `test_every_setting_can_be_overridden_from_the_command_line`: args `["voice.wav", "--mouth-gain-db", "20", "--release-s", "0.1", "--mouth-lead-ms", "40"]`; assert `(settings.mouth_gain_db, settings.release_s, settings.mouth_lead_ms) == (20.0, 0.1, 40.0)`.
- `test_impossible_override_is_rejected_before_touching_hardware`: args `["voice.wav", "--attack-s", "0"]`; assert `"attack_s"` in stderr.

In `tests/test_talk_loop.py`: the `LOUD` comment becomes `# about -4.3 dBFS: opens the mouth fully at the default gain once the envelope has risen`.

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_lip_sync.py tests/test_talk_settings.py tests/test_main.py tests/test_lipsync_wav.py tests/test_talk_loop.py`
Expected: FAIL — `check_mouth_gain` can't be imported; `mouth_gain_db` defaults to 0; the removed fields still exist.

- [ ] **Step 3: Implement**

`jack/show/audio/talk_settings.py`: module docstring's "except the gates (measured from Boss's voice)" → "except the mouth gain (set from Boss's measured voice)"; remove `open_curve`, `gate_open_db`, `gate_close_db`, `full_db`, the gate-order check, the `FLOOR_DB` gain check and its import, and the `open_curve` check; the mouth gain field becomes:

```python
    # The mouth's volts are max_v x amplitude x this gain (as a multiplier), capped at max_v: +14 dB opens the
    # mouth fully at -14 dBFS, the full-open point measured from Boss's voice. Changes the mouth, not the sound.
    mouth_gain_db: float = 14.0
```

`jack/show/audio/lip_sync.py` — replace the whole module with:

```python
"""Turns the audio's smoothed level into mouth voltage, one 20 ms tick at a time.

The voltage follows the audio's amplitude, as on the Talking Skull board: max_v x amplitude x gain, capped at
max_v. Below min_v the mouth gets a short closing pull (its rest pulse), then brakes; it holds closed unpowered.
Opening wider is slew-limited, and an opening that spends the mouth's stall budget closes until a quiet moment.
See "Mouth control" in SPEC.md.
"""

from jack.show.audio.envelope import FLOOR_DB
from jack.show.audio.pcm import TICK_S
from jack.show.audio.talk_settings import TalkSettings
from jack.show.motion.poses import MotorProfile
from jack.show.motion.stall_budget import StallBudget


def check_mouth_gain(settings: TalkSettings, mouth: MotorProfile) -> None:
    """Refuse a gain at which silence would open the mouth: it would then never close."""
    silence_v = mouth.max_v * 10 ** ((FLOOR_DB + settings.mouth_gain_db) / 20)
    if silence_v >= mouth.min_v:
        raise ValueError(
            f"mouth_gain_db {settings.mouth_gain_db} would open the mouth on silence ({FLOOR_DB} dBFS maps to "
            f"{silence_v:.2f} V, at or above the mouth's min_v {mouth.min_v} V)"
        )


class MouthController:
    """Signed volts for the mouth from the smoothed level (dBFS), opening in the direction of its `sign`."""

    def __init__(self, settings: TalkSettings, mouth: MotorProfile):
        check_mouth_gain(settings, mouth)
        self._mouth = mouth
        self._gain = 10 ** (settings.mouth_gain_db / 20)
        self._slew_per_tick = mouth.slew_v_per_s * TICK_S
        self._pull_ticks = mouth.rest_pulse_ticks
        self._magnitude = 0.0
        self._pull_left = 0
        self._budget = StallBudget(mouth)
        # Set when an opening spent the budget; the mouth stays closed until the sound drops below min_v.
        self._waiting_for_quiet = False

    def update(self, level_db: float) -> float:
        """Advance one tick with the current smoothed level and return the mouth voltage."""
        target = min(self._mouth.max_v, self._mouth.max_v * 10 ** (level_db / 20) * self._gain)
        if target < self._mouth.min_v:
            self._waiting_for_quiet = False
            return self._close()
        if self._waiting_for_quiet:
            return self._close()
        self._pull_left = 0
        # Opening wider is slew-limited because stepping straight to fully open strains the motor.
        self._magnitude = min(target, self._magnitude + self._slew_per_tick)
        volts = self._mouth.sign * self._magnitude
        self._budget.spend(volts)
        if not self._budget.exhausted:
            return volts
        self._waiting_for_quiet = True
        return self._close()

    def _close(self) -> float:
        """The closing pull after an opening, then 0 V (short brake)."""
        if self._magnitude > 0:
            self._magnitude = 0.0
            self._pull_left = self._pull_ticks
            self._budget.refill()
        if self._pull_left > 0:
            self._pull_left -= 1
            return self._mouth.rest_pulse_v
        return 0.0
```

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_lip_sync.py tests/test_talk_settings.py tests/test_main.py tests/test_lipsync_wav.py tests/test_talk_loop.py` then `.venv/bin/python -m pytest -q -W error`
Expected: all PASS. If any `tests/test_talk_loop.py` test fails because the mouth now opens to a different voltage for its fixed frames, recompute that test's expected mouth volts from the new mapping (`6 × 10^((level+14)/20)`, capped, slew 48 V/s) and record each changed expectation in a ledgered ruling — do not loosen the assertion.

- [ ] **Step 5: Commit**

```bash
git add jack/show/audio/lip_sync.py jack/show/audio/talk_settings.py tests/test_lip_sync.py tests/test_talk_settings.py tests/test_main.py tests/test_lipsync_wav.py tests/test_talk_loop.py
git commit -m "Lip sync follows the audio's amplitude, with a short closing pull"
```

---

### Task 2: Startup checks and docs

**Files:**
- Modify: `main.py`, `lipsync_wav.py`, `README.md`
- Test: `tests/test_main.py`, `tests/test_lipsync_wav.py`

**Interfaces:**
- Consumes: `check_mouth_gain(settings, mouth)` from Task 1.
- Produces: `main.REPLACED_BY_MOUTH_GAIN: tuple[str, ...] = ("gate_open_db", "gate_close_db", "full_db", "open_curve")`; `main.check_mouth_settings(settings: TalkSettings, profiles: Mapping[str, MotorProfile]) -> None` (raises `SystemExit`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
@pytest.mark.parametrize("var", ["JACK_GATE_OPEN_DB", "JACK_GATE_CLOSE_DB", "JACK_FULL_DB", "JACK_OPEN_CURVE"])
def test_a_replaced_lip_sync_setting_stops_the_app_saying_what_replaced_it(var):
    with pytest.raises(SystemExit) as exit_info:
        main.talk_settings({var: "-20"})
    message = str(exit_info.value.code)
    assert var in message and "JACK_MOUTH_GAIN_DB" in message and "amplitude" in message


def test_a_mouth_gain_that_would_open_the_mouth_on_silence_stops_the_app():
    with pytest.raises(SystemExit) as exit_info:
        main.check_mouth_settings(TalkSettings(mouth_gain_db=80.0), {"mouth": MOUTH})
    assert "mouth_gain_db" in str(exit_info.value.code)
    main.check_mouth_settings(TalkSettings(), {"mouth": MOUTH})
```

(import `from tests.profiles import MOUTH` at the top of `tests/test_main.py`).

Append to `tests/test_lipsync_wav.py`:

```python
def test_a_mouth_gain_that_would_open_the_mouth_on_silence_is_rejected_before_touching_hardware(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main(["voice.wav", "--mouth-gain-db", "80"]) == 2
    assert "mouth_gain_db" in capsys.readouterr().err
```

- [ ] **Step 2: Run them to see them fail**

Run: `.venv/bin/python -m pytest -q -W error tests/test_main.py tests/test_lipsync_wav.py`
Expected: FAIL — replaced settings are silently ignored; `check_mouth_settings` doesn't exist; `lipsync_wav` doesn't check the gain before running.

- [ ] **Step 3: Implement**

`main.py`:

1. Import `from jack.show.audio.lip_sync import check_mouth_gain`.
2. After `MOVED_TO_POSES`, add:

```python
# Lip-sync settings replaced when the mouth began following the audio's amplitude (SPEC.md "Mouth control").
REPLACED_BY_MOUTH_GAIN = ("gate_open_db", "gate_close_db", "full_db", "open_curve")
```

3. In `talk_settings`, after the `MOVED_TO_POSES` loop:

```python
    for field in REPLACED_BY_MOUTH_GAIN:
        var = SETTING_ENV_PREFIX + field.upper()
        if environ.get(var):
            raise SystemExit(
                f"{var} is gone: lip sync now follows the audio's amplitude. Remove it from /etc/jack/jack.env, "
                "tune JACK_MOUTH_GAIN_DB instead, then: sudo systemctl restart jack"
            )
```

4. Change `talk_settings`'s docstring example to `JACK_MOUTH_GAIN_DB=20`.
5. Add after `motor_profiles`:

```python
def check_mouth_settings(settings: TalkSettings, profiles: Mapping[str, MotorProfile]) -> None:
    """Lip sync's gain against the mouth's volts, or a one-line exit naming the problem."""
    try:
        check_mouth_gain(settings, profiles["mouth"])
    except ValueError as error:
        raise SystemExit(f"Mouth settings from /etc/jack/jack.env are invalid: {error}") from error
```

6. In `main()`, right after `profiles = motor_profiles()` (the settings are read before it), call `check_mouth_settings(settings, profiles)`.

`lipsync_wav.py`: import `from jack.show.audio.lip_sync import check_mouth_gain`; in the `try:` block of `main`, after `profiles = load_profiles(...)`, add `check_mouth_gain(settings, profiles["mouth"])`.

`README.md` "Tuning the lip sync": replace the opening paragraph with

```markdown
The mouth's voltage follows the audio's amplitude: twice as loud (in amplitude) opens it twice as far,
up to fully open. `JACK_MOUTH_GAIN_DB` in `/etc/jack/jack.env` sets how far a sound opens it (default
`14`: a −14 dBFS sound opens it fully; each +6 dB doubles how far any sound opens it). If the mouth
barely moves, raise it; if it opens on background noise or never closes, lower it. It changes only the
mouth; the sound from Jack's speaker doesn't change. A gain at which silence would open the mouth is
refused at startup. When the sound drops below the mouth's smallest opening, the mouth gets a short
closing pull (`rest_pulse_v`/`rest_pulse_s` under `[mouth]` in `/etc/jack/poses.toml`), then brakes.
```

and change the example block's lines `JACK_GATE_OPEN_DB=-20` / `JACK_OPEN_CURVE=1.5` to `JACK_MOUTH_GAIN_DB=20` / `JACK_ATTACK_S=0.02`; add, after the sentence listing the old `JACK_OPEN_MIN_V`… variables: `` (and likewise if the replaced `JACK_GATE_OPEN_DB`, `JACK_GATE_CLOSE_DB`, `JACK_FULL_DB` or `JACK_OPEN_CURVE` is still set) ``.

- [ ] **Step 4: Run the tests, then the full suite**

Run: `.venv/bin/python -m pytest -q -W error tests/test_main.py tests/test_lipsync_wav.py` then `.venv/bin/python -m pytest -q -W error`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py lipsync_wav.py README.md tests/test_main.py tests/test_lipsync_wav.py
git commit -m "Refuse replaced lip-sync settings and a gain that opens the mouth on silence"
```

- [ ] **Step 6 (only after Boss asks for the push): on the Pi**

The Pi's `jack.env` has `JACK_MOUTH_GAIN_DB` lines (10, 20, 30; the last wins). Under the new meaning 30 opens the mouth fully at −30 dBFS. With Boss's go-ahead, replace them with one line (Boss picks the value; the default is 14, so removing them gives 14), writing atomically and syncing:
`ssh -t 10.10.0.54 "sudo sed -i '/^JACK_MOUTH_GAIN_DB=/d' /etc/jack/jack.env && sync && sudo systemctl restart jack"`
Then check `journalctl -u jack -n 5` shows the startup line with no error.
