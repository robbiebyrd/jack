# Jack Talks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Boss talks into a Mumble client on the LAN; Jack plays the voice from its 3.5 mm jack and moves the animatronic mouth (motor B) in sync with its loudness.

**Architecture:** One talk loop, paced by the sound card's blocking write, runs every 20 ms. Each tick it mixes the next frame from every `VoiceSource`, measures its loudness (RMS in dBFS, attack/release smoothed), turns that into a mouth voltage with a small state machine (gate with hysteresis, proportional open, close pulse, slew limit, stall guard), drives motor B and plays the frame. The domain is pure Python; pymumble and pyalsaaudio sit in thin adapters that are the only modules importing them.

**Tech Stack:** Python 3.13 (Pi: Debian trixie), pytest on the Mac, `mumble-server` 1.5.735, pymumble (pinned git commit, pip in a venv), `python3-alsaaudio` 0.10.0, `python3-opuslib`, `python3-protobuf`, smbus2, systemd.

**Spec:** `SPEC.md` (sections "Talking", "Code structure", "Testing", "Deployment", "Out of scope"). Read it before any task.

## Global Constraints

- Work directly on `main`; no branches or worktrees.
- Other Claude sessions may commit in this checkout. Before every commit run `git status --short` and `git log --oneline -3`; stage only the files your task names (never `git add -A` / `git add .`); never revert or overwrite changes you didn't make.
- Never push. Pushing `main` deploys to the Pi (10.10.0.54) within about 60 s; only Boss decides when (Task 11).
- Run tests with `.venv/bin/pytest` from the repo root. Output must be pristine: no warnings, no stray prints.
- TDD for every code change: failing test first, see it fail for the right reason, minimal code, see it pass.
- No new dev dependencies. `alsaaudio` and `pymumble_py3` are imported only inside the functions that open real hardware/network (`open_alsa_sink`, `connect_mumble`), so the Mac test suite never needs them.
- Audio format everywhere: mono, signed 16-bit, native byte order, 48 000 Hz; one frame = 20 ms = 960 samples = 1920 bytes.
- Supply is 12 V. Motor B is the mouth; negative volts open it. Motor A holds 0 V.
- Every duration in `TalkSettings` is a whole number of 20 ms ticks.
- pymumble is pinned to commit `a560e6013dfbccb3666ce8756e1ca6b790bf05c8` and installed with `--no-deps` (apt supplies opuslib and protobuf), unless Task 1 records otherwise.
- Match the surrounding style: module docstring on every module, type hints, short functions, comments only for what/why.
- Secrets never go in the repo (it is public). The Mumble password lives only in `/etc/jack/jack.env` and `/etc/mumble/mumble-server.ini` on the Pi.

## Review Focus

1. A tuning WAV in the wrong format (stereo, 44.1 kHz, 8-bit) → a clear message saying what's needed and how to convert, exit code 2, and the motor never moves. Test in Task 7.
2. A tuning override that's impossible (`--open-max-v 20` on a 12 V supply, `--open-min-v 3 --open-max-v 2`, gates inverted, a duration that isn't whole ticks) → rejected with a message before the motor moves. Tests in Task 3 (settings) and Task 5 (supply check).
3. Fresh install with `JACK_MUMBLE_PASSWORD` empty → the app exits with a one-line message naming `/etc/jack/jack.env`, not a traceback; systemd keeps retrying. Test in Task 9.
4. Mumble chunks that aren't whole frames (odd byte counts, 10 ms or 60 ms packets) → re-cut into exact 20 ms frames with no bytes lost or reordered. Test in Task 4.
5. A deploy whose new pymumble pin fails to install → the checkout stays on the old commit and the app isn't restarted; the next poll retries. Test in Task 10.

---

### Task 1: Feasibility spike on the Pi (throwaway, with Boss)

Nothing from this task is committed except findings written into `SPEC.md`. Boss runs the `sudo` steps (sudo needs his password). Claude can run non-root commands over `ssh 10.10.0.54`.

**Files:**
- Modify: `SPEC.md` (append a "Spike findings (2026-09-28)" subsection under "Step 0: feasibility spike")

- [ ] **Step 1: Boss installs the server and libraries**

Boss runs on the Pi:

```bash
sudo apt-get update
sudo apt-get install -y mumble-server python3-alsaaudio python3-opuslib python3-protobuf python3-venv
sudo sed -i 's/^;\?serverpassword=.*/serverpassword=spike-only-password/' /etc/mumble/mumble-server.ini
sudo systemctl restart mumble-server
grep '^serverpassword=' /etc/mumble/mumble-server.ini
```

Expected: the last line prints `serverpassword=spike-only-password`. If the `grep` prints nothing, the ini uses a different key layout; stop and read `/etc/mumble/mumble-server.ini` with Boss.

- [ ] **Step 2: Create a throwaway venv and install pymumble without dependencies**

```bash
ssh 10.10.0.54 'python3 -m venv --system-site-packages /tmp/spike-venv && /tmp/spike-venv/bin/pip install --no-deps "pymumble @ git+https://github.com/azlux/pymumble@a560e6013dfbccb3666ce8756e1ca6b790bf05c8" && /tmp/spike-venv/bin/python -c "import pymumble_py3, opuslib, google.protobuf; print(google.protobuf.__version__)"'
```

Expected: install succeeds; the import prints `3.21.12`. If `import opuslib` fails with a missing `libopus` error, Boss runs `sudo apt-get install -y libopus0` and retry. If `import pymumble_py3` fails on protobuf, record the error and stop: the pin/`--no-deps` constraint must be revisited with Boss.

- [ ] **Step 3: Write the spike script on the Pi**

Create `/tmp/spike.py` on the Pi (via `ssh 10.10.0.54 'cat > /tmp/spike.py' <<'EOF' … EOF`):

```python
"""Throwaway: join Mumble as Jack, print what arrives, play it on the headphone jack."""
import collections
import sys
import threading
import time

import alsaaudio
import pymumble_py3 as pymumble
from pymumble_py3.constants import (
    PYMUMBLE_CLBK_CONNECTED,
    PYMUMBLE_CLBK_DISCONNECTED,
    PYMUMBLE_CLBK_SOUNDRECEIVED,
)

DEVICE = sys.argv[1] if len(sys.argv) > 1 else "plughw:CARD=Headphones,DEV=0"
PERIODS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
FRAME_BYTES = 1920
buffer = collections.deque()
lock = threading.Lock()
sizes = collections.Counter()


def on_sound(user, chunk):
    sizes[len(chunk.pcm)] += 1
    with lock:
        buffer.append(chunk.pcm)


client = pymumble.Mumble("127.0.0.1", "Jack", password="spike-only-password", reconnect=True, client_type=1)
client.daemon = True
client.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, on_sound)
client.callbacks.set_callback(PYMUMBLE_CLBK_CONNECTED, lambda: print("connected", flush=True))
client.callbacks.set_callback(PYMUMBLE_CLBK_DISCONNECTED, lambda: print("disconnected", flush=True))
client.set_receive_sound(True)
client.start()

pcm = alsaaudio.PCM(
    type=alsaaudio.PCM_PLAYBACK, mode=alsaaudio.PCM_NORMAL, rate=48000, channels=1,
    format=alsaaudio.PCM_FORMAT_S16_LE, periodsize=960, periods=PERIODS, device=DEVICE,
)
print("alsa info:", pcm.info(), flush=True)
pending = b""
last_report = time.monotonic()
while True:
    with lock:
        while buffer:
            pending += buffer.popleft()
    frame, pending = (pending[:FRAME_BYTES], pending[FRAME_BYTES:]) if len(pending) >= FRAME_BYTES else (bytes(FRAME_BYTES), pending)
    try:
        pcm.write(frame)
    except alsaaudio.ALSAAudioError as error:
        print("alsa error:", error, flush=True)
    if time.monotonic() - last_report > 5:
        print("chunk sizes seen:", dict(sizes), "backlog bytes:", len(pending), flush=True)
        last_report = time.monotonic()
```

- [ ] **Step 4: Run it and have Boss talk**

Boss stops the app so the tools don't fight: `sudo systemctl stop jack`. Then run (as Boss's user, which must be in group `audio`; check with `id`):

```bash
ssh -t 10.10.0.54 /tmp/spike-venv/bin/python /tmp/spike.py
```

Boss connects the Mumble desktop client to `10.10.0.54`, port `64738`, password `spike-only-password`, and talks with push-to-talk.

Record:
1. `connected` printed? (spec item 1)
2. `chunk sizes seen` values; 1920 means 20 ms packets. PCM rate/width/channels are fixed by pymumble's source at 48 kHz/16-bit/mono (spec item 2).
3. Voice heard from the jack? The `alsa info` line's period size and periods. If audio fails with `plughw:CARD=Headphones,DEV=0`, retry with `hw:0,0` and `default` (pass as argv[1]); record which works (spec item 3).
4. Boss runs `sudo systemctl restart mumble-server`; does `disconnected` then `connected` appear within about 15 s, and does voice resume? (spec item 4)
5. Boss's judgement of the delay, at start and after 30 minutes connected; does the `backlog bytes` number grow over time? (spec item 5)
6. Any choppiness over Wi-Fi; any `alsa error:` lines (underruns print as `Broken pipe`). (spec item 6)

- [ ] **Step 5: Decide**

If items 1–3 fail, reconnect (4) doesn't recover, or latency visibly drifts (5): stop here and take the findings to Boss (fallback considered: WebRTC with `aiortc`). Otherwise continue.

- [ ] **Step 6: Clean up the Pi**

```bash
ssh 10.10.0.54 'rm -rf /tmp/spike.py /tmp/spike-venv'
```

Boss restores the demo: `sudo systemctl start jack`. Leave `mumble-server` installed; Task 11 sets the real password.

- [ ] **Step 7: Record findings in the spec and commit**

Append under "Step 0: feasibility spike (throwaway)" in `SPEC.md` a subsection `#### Spike findings (2026-09-28)` listing the six results above with the exact values seen (chunk sizes, working ALSA device string, period size and periods from `pcm.info()`, reconnect time, latency judgement, choppiness). If the working device or periods differ from `plughw:CARD=Headphones,DEV=0` / `4`, Task 9 uses the recorded values.

```bash
git status --short && git log --oneline -3
git add SPEC.md
git commit -m "Spec: record the pymumble and ALSA spike findings from the Pi"
```

---

### Task 2: PCM frames and loudness envelope

**Files:**
- Create: `motor_test/pcm.py`, `motor_test/envelope.py`, `tests/audio.py`
- Test: `tests/test_pcm.py`, `tests/test_envelope.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `motor_test.pcm`: `SAMPLE_RATE_HZ = 48000`, `TICK_S = 0.02`, `FRAME_SAMPLES = 960`, `SAMPLE_BYTES = 2`, `FRAME_BYTES = 1920`, `TICKS_PER_SECOND = 50`, `silence() -> bytes`, `mix(frames: Sequence[bytes]) -> bytes`.
  - `motor_test.envelope`: `FLOOR_DB = -90.0`, `rms_dbfs(frame: bytes) -> float`, `EnvelopeFollower(attack_s: float, release_s: float, tick_s: float)` with `.level_db: float` and `.update(db: float) -> float`.
  - `tests.audio`: `constant_frame(value: int) -> bytes`, `sine_frame(amplitude: int, frequency_hz: float = 1000.0) -> bytes`.

- [ ] **Step 1: Write the test helpers**

`tests/audio.py`:

```python
"""Builders for PCM test frames in the talk loop's format."""

import math
from array import array

from motor_test.pcm import FRAME_SAMPLES, SAMPLE_RATE_HZ


def constant_frame(value: int) -> bytes:
    """One frame with every sample equal to `value`."""
    return array("h", [value] * FRAME_SAMPLES).tobytes()


def sine_frame(amplitude: int, frequency_hz: float = 1000.0) -> bytes:
    """One frame of a sine wave; 1 kHz fits exactly 20 whole cycles in a 20 ms frame."""
    return array(
        "h",
        (round(amplitude * math.sin(2 * math.pi * frequency_hz * i / SAMPLE_RATE_HZ)) for i in range(FRAME_SAMPLES)),
    ).tobytes()
```

- [ ] **Step 2: Write the failing PCM tests**

`tests/test_pcm.py`:

```python
import pytest

from motor_test.pcm import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE_HZ, TICK_S, TICKS_PER_SECOND, mix, silence
from tests.audio import constant_frame


def test_a_frame_is_20_ms_of_48_khz_mono_16_bit_audio():
    assert SAMPLE_RATE_HZ == 48000
    assert TICK_S == 0.02
    assert FRAME_SAMPLES == 960
    assert FRAME_BYTES == 1920
    assert TICKS_PER_SECOND == 50


def test_silence_is_one_frame_of_zeros():
    assert silence() == bytes(FRAME_BYTES)


def test_mixing_one_frame_returns_it_unchanged():
    frame = constant_frame(1234)
    assert mix([frame]) == frame


def test_mixing_sums_samples():
    assert mix([constant_frame(1000), constant_frame(-300)]) == constant_frame(700)


def test_mixing_clips_to_the_16_bit_range():
    assert mix([constant_frame(30000), constant_frame(30000)]) == constant_frame(32767)
    assert mix([constant_frame(-30000), constant_frame(-30000)]) == constant_frame(-32768)


def test_mixing_rejects_no_frames():
    with pytest.raises(ValueError):
        mix([])


@pytest.mark.parametrize("frames", [[bytes(10)], [constant_frame(0), bytes(10)]])
def test_mixing_rejects_a_frame_of_the_wrong_size(frames):
    with pytest.raises(ValueError):
        mix(frames)
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/bin/pytest tests/test_pcm.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'motor_test.pcm'`.

- [ ] **Step 4: Implement `motor_test/pcm.py`**

```python
"""Fixed-size PCM audio frames: what the talk loop plays, measures and mixes every tick.

Audio is mono, signed 16-bit, native byte order (little-endian on the Pi and the Mac),
48 kHz: the format Mumble decodes to.
"""

from array import array
from collections.abc import Sequence

SAMPLE_RATE_HZ = 48000
TICK_S = 0.02
FRAME_SAMPLES = round(SAMPLE_RATE_HZ * TICK_S)
SAMPLE_BYTES = 2
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_BYTES
TICKS_PER_SECOND = round(1 / TICK_S)
SAMPLE_MIN = -32768
SAMPLE_MAX = 32767


def silence() -> bytes:
    """One frame of digital silence."""
    return bytes(FRAME_BYTES)


def mix(frames: Sequence[bytes]) -> bytes:
    """Sum frames sample by sample, clipping to the 16-bit range."""
    if not frames:
        raise ValueError("mix needs at least one frame")
    for frame in frames:
        if len(frame) != FRAME_BYTES:
            raise ValueError(f"frames must be {FRAME_BYTES} bytes, got {len(frame)}")
    if len(frames) == 1:
        return frames[0]
    decoded = [array("h", frame) for frame in frames]
    mixed = array("h", (max(SAMPLE_MIN, min(SAMPLE_MAX, sum(samples))) for samples in zip(*decoded)))
    return mixed.tobytes()
```

- [ ] **Step 5: Run to see them pass**

Run: `.venv/bin/pytest tests/test_pcm.py -v`
Expected: 8 passed.

- [ ] **Step 6: Write the failing envelope tests**

`tests/test_envelope.py`:

```python
import math

import pytest

from motor_test.envelope import FLOOR_DB, EnvelopeFollower, rms_dbfs
from motor_test.pcm import silence
from tests.audio import constant_frame, sine_frame


def test_digital_silence_reads_as_the_floor():
    assert FLOOR_DB == -90.0
    assert rms_dbfs(silence()) == FLOOR_DB


def test_full_scale_square_reads_0_dbfs():
    assert rms_dbfs(constant_frame(32767)) == pytest.approx(0.0)


def test_full_scale_sine_reads_about_minus_3_dbfs():
    assert rms_dbfs(sine_frame(32767)) == pytest.approx(-3.0103, abs=0.01)


def test_half_amplitude_is_about_6_db_quieter():
    assert rms_dbfs(sine_frame(16384)) - rms_dbfs(sine_frame(32767)) == pytest.approx(-6.02, abs=0.01)


def test_sound_quieter_than_the_floor_reads_as_the_floor():
    assert rms_dbfs(constant_frame(1)) == FLOOR_DB


def test_follower_starts_at_the_floor():
    assert EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02).level_db == FLOOR_DB


def test_rising_level_follows_the_attack_time_constant():
    follower = EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02)
    assert follower.update(0.0) == pytest.approx(FLOOR_DB * math.exp(-0.02 / 0.01))


def test_falling_level_follows_the_release_time_constant():
    follower = EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02)
    for _ in range(50):
        follower.update(0.0)
    settled = follower.level_db
    expected = FLOOR_DB + (settled - FLOOR_DB) * math.exp(-0.02 / 0.08)
    assert follower.update(FLOOR_DB) == pytest.approx(expected)


@pytest.mark.parametrize("attack_s, release_s, tick_s", [(0, 0.08, 0.02), (0.01, -1, 0.02), (0.01, 0.08, 0)])
def test_time_constants_and_tick_must_be_positive(attack_s, release_s, tick_s):
    with pytest.raises(ValueError):
        EnvelopeFollower(attack_s, release_s, tick_s)
```

- [ ] **Step 7: Run to see them fail**

Run: `.venv/bin/pytest tests/test_envelope.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.envelope'`.

- [ ] **Step 8: Implement `motor_test/envelope.py`**

```python
"""How loud each frame is, smoothed like the Talking Skull board's peak detector but with chosen timings."""

import math
from array import array

FLOOR_DB = -90.0
FULL_SCALE = 32767


def rms_dbfs(frame: bytes) -> float:
    """RMS level of a 16-bit frame in dB relative to full scale, never below FLOOR_DB."""
    samples = array("h", frame)
    if not samples:
        return FLOOR_DB
    mean_square = sum(sample * sample for sample in samples) / len(samples)
    if mean_square == 0:
        return FLOOR_DB
    return max(FLOOR_DB, 10 * math.log10(mean_square / FULL_SCALE**2))


class EnvelopeFollower:
    """One-pole smoother in dB: rises with the attack time constant and falls with the release one."""

    def __init__(self, attack_s: float, release_s: float, tick_s: float):
        for name, value in (("attack_s", attack_s), ("release_s", release_s), ("tick_s", tick_s)):
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value}")
        self._attack = math.exp(-tick_s / attack_s)
        self._release = math.exp(-tick_s / release_s)
        self.level_db = FLOOR_DB

    def update(self, db: float) -> float:
        """Move the smoothed level one tick toward `db` and return it."""
        coefficient = self._attack if db > self.level_db else self._release
        self.level_db = db + (self.level_db - db) * coefficient
        return self.level_db
```

- [ ] **Step 9: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_pcm.py tests/test_envelope.py -v && .venv/bin/pytest`
Expected: all pass, no warnings.

- [ ] **Step 10: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/pcm.py motor_test/envelope.py tests/audio.py tests/test_pcm.py tests/test_envelope.py
git commit -m "Add 20 ms PCM frames with mixing, and a dBFS loudness envelope"
```

---

### Task 3: Talk settings and mouth control

**Files:**
- Modify: `motor_test/ramp.py:48-77` (extract `whole_steps`)
- Create: `motor_test/talk_settings.py`, `motor_test/lip_sync.py`
- Test: `tests/test_ramp.py` (add), `tests/test_talk_settings.py`, `tests/test_lip_sync.py`

**Interfaces:**
- Consumes: `motor_test.pcm.TICK_S`.
- Produces:
  - `motor_test.ramp.whole_steps(seconds: float, step_s: float) -> int` (raises `ValueError` unless `seconds` is a whole, non-zero number of steps).
  - `motor_test.talk_settings.TalkSettings` (frozen dataclass; fields `open_min_v, open_max_v, open_slew_v_per_s, close_v, close_s, stall_v, max_stall_s, attack_s, release_s, gate_open_db, gate_close_db, full_db, mouth_lead_ms, max_backlog_ms`, all `float`), with properties `close_ticks`, `max_stall_ticks`, `mouth_lead_ticks`, `max_backlog_frames` (all `int`).
  - `motor_test.lip_sync.MouthController(settings: TalkSettings)` with `.update(level_db: float) -> float` (signed volts for motor B; negative opens).

- [ ] **Step 1: Write the failing `whole_steps` tests**

Append to `tests/test_ramp.py` (add `whole_steps` to its existing `from motor_test.ramp import …` line):

```python
def test_whole_steps_counts_steps_in_a_duration():
    assert whole_steps(0.16, 0.02) == 8
    assert whole_steps(0.25, 0.05) == 5


@pytest.mark.parametrize("seconds", [0.15, 0.0, -0.02])
def test_whole_steps_rejects_partial_or_empty_durations(seconds):
    with pytest.raises(ValueError):
        whole_steps(seconds, 0.02)
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest tests/test_ramp.py -v`
Expected: `ImportError: cannot import name 'whole_steps'`.

- [ ] **Step 3: Extract `whole_steps` in `motor_test/ramp.py`**

Add after `volts_to_count`:

```python
def whole_steps(seconds: float, step_s: float) -> int:
    """How many `step_s` steps make `seconds`; it must be a whole, non-zero number."""
    steps = round(seconds / step_s)
    if steps < 1 or not math.isclose(steps * step_s, seconds):
        raise ValueError(f"{seconds} s is not a whole, non-zero number of {step_s} s steps")
    return steps
```

In `segment_profile`, replace

```python
        steps = round(seconds / step_s)
        if steps < 1 or not math.isclose(steps * step_s, seconds):
            raise ValueError(f"{seconds} s is not a whole, non-zero number of {step_s} s steps")
```

with

```python
        steps = whole_steps(seconds, step_s)
```

- [ ] **Step 4: Run to see them pass**

Run: `.venv/bin/pytest tests/test_ramp.py -v`
Expected: all pass (existing segment tests prove the refactor kept behavior).

- [ ] **Step 5: Write the failing settings tests**

`tests/test_talk_settings.py`:

```python
import dataclasses

import pytest

from motor_test.talk_settings import TalkSettings


def test_starting_values_match_the_spec():
    settings = TalkSettings()
    assert (settings.open_min_v, settings.open_max_v, settings.open_slew_v_per_s) == (2.0, 6.0, 24.0)
    assert (settings.close_v, settings.close_s) == (0.5, 0.16)
    assert (settings.stall_v, settings.max_stall_s) == (5.0, 0.5)
    assert (settings.attack_s, settings.release_s) == (0.01, 0.08)
    assert (settings.gate_open_db, settings.gate_close_db, settings.full_db) == (-35.0, -40.0, -10.0)
    assert (settings.mouth_lead_ms, settings.max_backlog_ms) == (0.0, 200.0)


def test_durations_convert_to_whole_20_ms_ticks():
    settings = TalkSettings(mouth_lead_ms=40.0)
    assert settings.close_ticks == 8
    assert settings.max_stall_ticks == 25
    assert settings.mouth_lead_ticks == 2
    assert settings.max_backlog_frames == 10


def test_no_mouth_lead_is_zero_ticks():
    assert TalkSettings().mouth_lead_ticks == 0


def test_settings_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        TalkSettings().close_v = 2.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"open_min_v": 0.0},
        {"open_min_v": 3.0, "open_max_v": 2.0},
        {"open_slew_v_per_s": 0.0},
        {"close_v": 0.0},
        {"stall_v": 0.0},
        {"attack_s": 0.0},
        {"release_s": -0.08},
        {"gate_close_db": -30.0},
        {"full_db": -40.0},
        {"close_s": 0.15},
        {"max_stall_s": 0.0},
        {"mouth_lead_ms": 30.0},
        {"mouth_lead_ms": -20.0},
        {"max_backlog_ms": 0.0},
    ],
)
def test_impossible_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        TalkSettings(**overrides)
```

- [ ] **Step 6: Run to see them fail**

Run: `.venv/bin/pytest tests/test_talk_settings.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.talk_settings'`.

- [ ] **Step 7: Implement `motor_test/talk_settings.py`**

```python
"""Every tunable setting of the talk loop in one place, checked when built.

Starting values are guesses to tune by eye with lipsync_wav.py, except the voltages
(Boss's calibration) and the close pulse (the random-speech demo Boss saw as lifelike).
See the "Mouth control" table in SPEC.md.
"""

from dataclasses import dataclass

from motor_test.pcm import TICK_S
from motor_test.ramp import whole_steps


@dataclass(frozen=True)
class TalkSettings:
    open_min_v: float = 2.0
    open_max_v: float = 6.0
    open_slew_v_per_s: float = 24.0
    close_v: float = 0.5
    close_s: float = 0.16
    stall_v: float = 5.0
    max_stall_s: float = 0.5
    attack_s: float = 0.01
    release_s: float = 0.08
    gate_open_db: float = -35.0
    gate_close_db: float = -40.0
    full_db: float = -10.0
    mouth_lead_ms: float = 0.0
    max_backlog_ms: float = 200.0

    def __post_init__(self) -> None:
        for name in ("open_min_v", "open_slew_v_per_s", "close_v", "stall_v", "attack_s", "release_s"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive, got {getattr(self, name)}")
        if self.open_max_v < self.open_min_v:
            raise ValueError(f"open_max_v ({self.open_max_v}) must be at least open_min_v ({self.open_min_v})")
        if not self.gate_close_db < self.gate_open_db < self.full_db:
            raise ValueError(
                "gates must satisfy gate_close_db < gate_open_db < full_db, got "
                f"{self.gate_close_db}, {self.gate_open_db}, {self.full_db}"
            )
        if self.mouth_lead_ms < 0:
            raise ValueError(f"mouth_lead_ms must not be negative, got {self.mouth_lead_ms}")
        # Each property below raises ValueError for a duration that isn't whole ticks.
        self.close_ticks
        self.max_stall_ticks
        self.mouth_lead_ticks
        self.max_backlog_frames

    @property
    def close_ticks(self) -> int:
        return whole_steps(self.close_s, TICK_S)

    @property
    def max_stall_ticks(self) -> int:
        return whole_steps(self.max_stall_s, TICK_S)

    @property
    def mouth_lead_ticks(self) -> int:
        if self.mouth_lead_ms == 0:
            return 0
        return whole_steps(self.mouth_lead_ms / 1000, TICK_S)

    @property
    def max_backlog_frames(self) -> int:
        return whole_steps(self.max_backlog_ms / 1000, TICK_S)
```

- [ ] **Step 8: Run to see them pass**

Run: `.venv/bin/pytest tests/test_talk_settings.py -v`
Expected: all pass.

- [ ] **Step 9: Write the failing mouth-control tests**

`tests/test_lip_sync.py`:

```python
import pytest

from motor_test.lip_sync import MouthController
from motor_test.talk_settings import TalkSettings

QUIET = -60.0
GATE = -35.0  # default gate_open_db
BETWEEN_GATES = -38.0  # between gate_close_db (-40) and gate_open_db
BELOW_CLOSE = -45.0
FULL = -10.0  # default full_db


def unslewed(**overrides):
    """A controller whose opening isn't slew-limited, so each test sees target voltages directly."""
    return MouthController(TalkSettings(open_slew_v_per_s=1000.0, **overrides))


def run(controller, levels):
    return [controller.update(level) for level in levels]


def test_mouth_stays_closed_while_quiet():
    assert run(unslewed(), [QUIET] * 3) == [0.0, 0.0, 0.0]


def test_reaching_the_gate_opens_to_relaxed_open():
    assert unslewed().update(GATE) == -2.0


def test_full_level_opens_fully_and_louder_stays_at_fully_open():
    assert run(unslewed(), [FULL, 0.0]) == [-6.0, -6.0]


def test_opening_is_proportional_to_loudness_between_gate_and_full():
    assert unslewed().update(-22.5) == pytest.approx(-4.0)


def test_between_the_gates_an_open_mouth_stays_open():
    assert run(unslewed(), [GATE, BETWEEN_GATES]) == [-2.0, -2.0]


def test_between_the_gates_a_closed_mouth_stays_closed():
    assert unslewed().update(BETWEEN_GATES) == 0.0


def test_falling_below_the_close_gate_pulses_closed_then_rests():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [GATE] + [BELOW_CLOSE] * (settings.close_ticks + 2))
    assert volts == [-2.0] + [0.5] * settings.close_ticks + [0.0, 0.0]


def test_next_syllable_interrupts_the_close_pulse():
    assert run(unslewed(), [GATE, BELOW_CLOSE, GATE]) == [-2.0, 0.5, -2.0]


def test_opening_is_slew_limited_to_24_volts_per_second():
    controller = MouthController(TalkSettings())
    assert run(controller, [FULL] * 3) == pytest.approx([-0.48, -0.96, -1.44])


def test_closing_down_to_a_smaller_opening_is_not_slew_limited():
    controller = MouthController(TalkSettings())
    run(controller, [FULL] * 20)
    assert controller.update(GATE) == -2.0


def test_stall_guard_caps_a_long_full_open_at_relaxed_open():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    volts = run(MouthController(settings), [FULL] * (settings.max_stall_ticks + 2))
    assert volts == [-6.0] * settings.max_stall_ticks + [-2.0, -2.0]


def test_stall_guard_resets_after_the_mouth_closes():
    settings = TalkSettings(open_slew_v_per_s=1000.0)
    controller = MouthController(settings)
    run(controller, [FULL] * (settings.max_stall_ticks + 1))
    run(controller, [BELOW_CLOSE] * (settings.close_ticks + 1))
    assert controller.update(FULL) == -6.0


def test_a_break_below_the_stall_voltage_restarts_the_stall_timer():
    controller = unslewed()
    run(controller, [FULL] * 20)
    controller.update(GATE)
    assert run(controller, [FULL] * 20) == [-6.0] * 20
```

- [ ] **Step 10: Run to see them fail**

Run: `.venv/bin/pytest tests/test_lip_sync.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.lip_sync'`.

- [ ] **Step 11: Implement `motor_test/lip_sync.py`**

```python
"""Turns smoothed loudness into mouth voltage, one 20 ms tick at a time.

Closed until the level reaches the open gate; open in proportion to loudness while it
stays above the (lower) close gate; then a short closing pulse. See "Mouth control" in SPEC.md.
"""

from enum import Enum

from motor_test.pcm import TICK_S
from motor_test.talk_settings import TalkSettings


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    CLOSING = "closing"


class MouthController:
    """State machine from level (dBFS) to signed volts for the mouth motor; negative opens."""

    def __init__(self, settings: TalkSettings):
        self._settings = settings
        self._slew_per_tick = settings.open_slew_v_per_s * TICK_S
        self._state = _State.CLOSED
        self._magnitude = 0.0
        self._close_ticks_left = 0
        self._stall_ticks = 0
        self._stall_capped = False

    def update(self, level_db: float) -> float:
        """Advance one tick with the current smoothed level and return the mouth voltage."""
        if level_db >= self._settings.gate_open_db:
            self._state = _State.OPEN
        elif self._state is _State.OPEN and level_db < self._settings.gate_close_db:
            self._start_closing()

        if self._state is _State.OPEN:
            return -self._open_magnitude(level_db)
        if self._state is _State.CLOSING:
            return self._closing_volts()
        return 0.0

    def _open_magnitude(self, level_db: float) -> float:
        s = self._settings
        loudness = min(1.0, max(0.0, (level_db - s.gate_open_db) / (s.full_db - s.gate_open_db)))
        target = s.open_min_v + (s.open_max_v - s.open_min_v) * loudness
        if self._stall_capped:
            target = min(target, s.open_min_v)
        # Opening wider is slew-limited because stepping straight to fully open strains the motor.
        self._magnitude = min(target, self._magnitude + self._slew_per_tick)
        self._guard_stall()
        return self._magnitude

    def _guard_stall(self) -> None:
        """Cap the opening at relaxed open once it has pushed past stall_v for too long without a break."""
        s = self._settings
        if self._magnitude <= s.stall_v:
            self._stall_ticks = 0
            return
        self._stall_ticks += 1
        if self._stall_ticks > s.max_stall_ticks:
            self._stall_capped = True
            self._magnitude = min(self._magnitude, s.open_min_v)

    def _start_closing(self) -> None:
        self._state = _State.CLOSING
        self._close_ticks_left = self._settings.close_ticks
        self._magnitude = 0.0
        self._stall_ticks = 0
        self._stall_capped = False

    def _closing_volts(self) -> float:
        self._close_ticks_left -= 1
        if self._close_ticks_left == 0:
            self._state = _State.CLOSED
        return self._settings.close_v
```

- [ ] **Step 12: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_lip_sync.py tests/test_talk_settings.py tests/test_ramp.py -v && .venv/bin/pytest`
Expected: all pass, no warnings.

- [ ] **Step 13: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/ramp.py motor_test/talk_settings.py motor_test/lip_sync.py tests/test_ramp.py tests/test_talk_settings.py tests/test_lip_sync.py
git commit -m "Add talk settings and the mouth state machine: gate, proportional open, close pulse, slew and stall limits"
```

---

### Task 4: Frame queue

**Files:**
- Create: `motor_test/frame_queue.py`
- Test: `tests/test_frame_queue.py`

**Interfaces:**
- Consumes: `motor_test.pcm.FRAME_BYTES`.
- Produces: `motor_test.frame_queue.FrameQueue(max_frames: int)` with `.put(pcm: bytes) -> int` (returns how many oldest frames were dropped) and `.take() -> bytes | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_frame_queue.py`:

```python
import threading

import pytest

from motor_test.frame_queue import FrameQueue
from motor_test.pcm import FRAME_BYTES
from tests.audio import constant_frame


def test_empty_queue_has_nothing_to_take():
    assert FrameQueue(max_frames=10).take() is None


def test_whole_frames_come_out_in_order():
    queue = FrameQueue(max_frames=10)
    queue.put(constant_frame(1) + constant_frame(2))
    assert [queue.take(), queue.take(), queue.take()] == [constant_frame(1), constant_frame(2), None]


def test_a_partial_frame_waits_for_the_rest():
    queue = FrameQueue(max_frames=10)
    data = constant_frame(1) + constant_frame(2)
    queue.put(data[: FRAME_BYTES + 700])
    assert queue.take() == constant_frame(1)
    assert queue.take() is None
    queue.put(data[FRAME_BYTES + 700 :])
    assert queue.take() == constant_frame(2)


def test_odd_byte_counts_lose_and_reorder_nothing():
    queue = FrameQueue(max_frames=10)
    data = constant_frame(1) + constant_frame(-2) + constant_frame(3)
    for start in range(0, len(data), 333):
        queue.put(data[start : start + 333])
    assert [queue.take(), queue.take(), queue.take(), queue.take()] == [
        constant_frame(1),
        constant_frame(-2),
        constant_frame(3),
        None,
    ]


def test_backlog_past_the_limit_drops_the_oldest_frames():
    queue = FrameQueue(max_frames=3)
    dropped = queue.put(b"".join(constant_frame(value) for value in range(5)))
    assert dropped == 2
    assert [queue.take(), queue.take(), queue.take(), queue.take()] == [
        constant_frame(2),
        constant_frame(3),
        constant_frame(4),
        None,
    ]


def test_nothing_is_dropped_within_the_limit():
    assert FrameQueue(max_frames=3).put(constant_frame(0) * 3) == 0


def test_concurrent_puts_keep_every_frame():
    queue = FrameQueue(max_frames=10_000)

    def put_frames():
        for _ in range(500):
            queue.put(constant_frame(7))

    threads = [threading.Thread(target=put_frames) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    taken = 0
    while queue.take() is not None:
        taken += 1
    assert taken == 1000


def test_limit_must_be_at_least_one_frame():
    with pytest.raises(ValueError):
        FrameQueue(max_frames=0)
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest tests/test_frame_queue.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.frame_queue'`.

- [ ] **Step 3: Implement `motor_test/frame_queue.py`**

```python
"""A bounded, thread-safe queue that cuts PCM chunks of any size into talk-loop frames.

A voice's network thread puts chunks in; the talk loop takes one frame per tick. When the
network delivers faster than the loop plays, the oldest audio is dropped so delay stays bounded.
"""

import threading
from collections import deque

from motor_test.pcm import FRAME_BYTES


class FrameQueue:
    def __init__(self, max_frames: int):
        if max_frames < 1:
            raise ValueError(f"max_frames must be at least 1, got {max_frames}")
        self._max_frames = max_frames
        self._frames: deque[bytes] = deque()
        self._partial = b""
        self._lock = threading.Lock()

    def put(self, pcm: bytes) -> int:
        """Append `pcm` as whole frames, keeping any remainder for the next chunk.

        Returns how many of the oldest frames were dropped to stay within max_frames.
        """
        with self._lock:
            data = self._partial + pcm
            whole = len(data) - len(data) % FRAME_BYTES
            for start in range(0, whole, FRAME_BYTES):
                self._frames.append(data[start : start + FRAME_BYTES])
            self._partial = data[whole:]
            dropped = max(0, len(self._frames) - self._max_frames)
            for _ in range(dropped):
                self._frames.popleft()
            return dropped

    def take(self) -> bytes | None:
        """The oldest whole frame, or None when there isn't one."""
        with self._lock:
            return self._frames.popleft() if self._frames else None
```

- [ ] **Step 4: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_frame_queue.py -v && .venv/bin/pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/frame_queue.py tests/test_frame_queue.py
git commit -m "Add a bounded, thread-safe frame queue that re-cuts voice chunks into 20 ms frames"
```

---

### Task 5: Ports and the talk loop

**Files:**
- Modify: `motor_test/ports.py`, `tests/fakes.py`, `tests/test_smoke_test.py`, `tests/test_calibration.py` (move the shared `drives` / `MotorThatFailsToStop` / `no_op` helpers into `tests/fakes.py`)
- Create: `motor_test/talk_loop.py`
- Test: `tests/test_talk_loop.py`

**Interfaces:**
- Consumes: `pcm.TICKS_PER_SECOND`, `pcm.mix`, `pcm.silence`; `envelope.EnvelopeFollower`, `envelope.rms_dbfs`; `lip_sync.MouthController`; `talk_settings.TalkSettings`; `ramp.volts_to_count`; `attempt_all.attempt_all`; `ports.MotorOutput`.
- Produces:
  - `motor_test.ports.VoiceSource` protocol: `take_frames() -> list[bytes]`.
  - `motor_test.ports.AudioSink` protocol: `write(frame: bytes) -> None`, `close() -> None`.
  - `motor_test.talk_loop.run_talk_loop(sources: Sequence[VoiceSource], sink: AudioSink, mouth: MotorOutput, idle_motors: Sequence[MotorOutput], settings: TalkSettings, supply_volts: float, on_second: Callable[[], None], until: Callable[[], bool] = lambda: False) -> None`.
  - `tests.fakes`: `RecordingSink(fail_on_write: int | None = None)` with `.frames`, `.closed`; `ScriptedSource(frames)`; `MotorThatFailsToStop`; `drives(motor) -> list[int]`; `no_op()`.

- [ ] **Step 1: Move the shared test helpers into `tests/fakes.py`**

Append to `tests/fakes.py` (and add `from collections import deque` at its top):

```python
class MotorThatFailsToStop(RecordingMotor):
    """RecordingMotor whose stop() records the call, then fails like a dropped I2C write."""

    def stop(self):
        super().stop()
        raise OSError("I2C write failed")


def drives(motor):
    """The signed counts a RecordingMotor was driven with, in order."""
    return [call[1] for call in motor.calls if call[0] == "drive"]


def no_op():
    pass


class RecordingSink:
    """AudioSink that records every frame written; write number `fail_on_write` raises instead."""

    def __init__(self, fail_on_write=None):
        self.frames = []
        self.closed = False
        self._fail_on_write = fail_on_write

    def write(self, frame):
        if self._fail_on_write is not None and len(self.frames) + 1 == self._fail_on_write:
            raise OSError("sound card gone")
        self.frames.append(frame)

    def close(self):
        self.closed = True


class ScriptedSource:
    """VoiceSource that sounds its frames one per tick, then goes quiet."""

    def __init__(self, frames):
        self._frames = deque(frames)

    def take_frames(self):
        return [self._frames.popleft()] if self._frames else []
```

In `tests/test_smoke_test.py`, delete its local `MotorThatFailsToStop`, `no_op` and `drives` definitions and import them: `from tests.fakes import MotorThatFailsToStop, RecordingMotor, drives, no_op`. In `tests/test_calibration.py`, delete its local `drives` (line 35) and import it from `tests.fakes` (keep whatever else it already imports from there).

Run: `.venv/bin/pytest`
Expected: all existing tests still pass (pure move).

- [ ] **Step 2: Add the ports**

In `motor_test/ports.py`, change the docstring to `"""Ports the application layer depends on."""` and append:

```python
class VoiceSource(Protocol):
    """Where voices come from: a Mumble channel, a WAV file, later pre-recorded clips."""

    def take_frames(self) -> list[bytes]:
        """The next frame from each voice currently sounding; empty when all are quiet."""
        ...


class AudioSink(Protocol):
    """Where the talk loop's audio goes."""

    def write(self, frame: bytes) -> None:
        """Play one frame, blocking while the device's buffer is full; this paces the talk loop."""
        ...

    def close(self) -> None: ...
```

- [ ] **Step 3: Write the failing talk-loop tests**

`tests/test_talk_loop.py`:

```python
import pytest

from motor_test.pcm import TICKS_PER_SECOND, silence
from motor_test.ramp import volts_to_count
from motor_test.talk_loop import run_talk_loop
from motor_test.talk_settings import TalkSettings
from tests.audio import constant_frame
from tests.fakes import MotorThatFailsToStop, RecordingMotor, RecordingSink, ScriptedSource, drives, no_op

SUPPLY_VOLTS = 12.0
LOUD = constant_frame(20000)  # about -4.3 dBFS: above full_db once the envelope has risen
FAST = TalkSettings(open_slew_v_per_s=1000.0)


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


def talk(sources, ticks, settings=FAST, sink=None, mouth=None, idle=None, on_second=no_op):
    sink = sink if sink is not None else RecordingSink()
    mouth = mouth if mouth is not None else RecordingMotor()
    idle = idle if idle is not None else RecordingMotor()
    run_talk_loop(sources, sink, mouth, [idle], settings, SUPPLY_VOLTS, on_second, stop_after(ticks))
    return sink, mouth, idle


def test_quiet_sources_play_silence_and_keep_the_mouth_closed():
    sink, mouth, idle = talk([ScriptedSource([])], ticks=3)
    assert sink.frames == [silence()] * 3
    assert drives(mouth) == [0, 0, 0]


def test_idle_motor_holds_zero_volts_then_brakes():
    _, _, idle = talk([ScriptedSource([])], ticks=3)
    assert idle.calls == [("drive", 0), ("stop",)]


def test_a_loud_voice_is_played_and_opens_the_mouth_fully():
    sink, mouth, _ = talk([ScriptedSource([LOUD, LOUD])], ticks=2)
    assert sink.frames == [LOUD, LOUD]
    assert drives(mouth)[0] < 0
    assert drives(mouth)[1] == volts_to_count(-6.0, SUPPLY_VOLTS)


def test_sources_sounding_together_are_mixed():
    sink, _, _ = talk([ScriptedSource([LOUD]), ScriptedSource([LOUD])], ticks=1)
    assert sink.frames == [constant_frame(32767)]


def test_mouth_lead_delays_the_audio_but_not_the_mouth():
    settings = TalkSettings(open_slew_v_per_s=1000.0, mouth_lead_ms=40.0)
    sink, mouth, _ = talk([ScriptedSource([LOUD] * 3)], ticks=3, settings=settings)
    assert sink.frames == [silence(), silence(), LOUD]
    assert drives(mouth)[0] < 0


@pytest.mark.parametrize("ticks, pings", [(TICKS_PER_SECOND - 1, 0), (TICKS_PER_SECOND, 1), (2 * TICKS_PER_SECOND, 2)])
def test_on_second_runs_once_per_second_of_audio(ticks, pings):
    calls = []
    talk([ScriptedSource([])], ticks=ticks, on_second=lambda: calls.append(1))
    assert len(calls) == pings


def test_a_sound_card_failure_brakes_both_motors_and_closes_the_sink():
    sink, mouth, idle = RecordingSink(fail_on_write=2), RecordingMotor(), RecordingMotor()
    with pytest.raises(OSError):
        talk([ScriptedSource([LOUD] * 5)], ticks=5, sink=sink, mouth=mouth, idle=idle)
    assert mouth.calls[-1] == ("stop",)
    assert idle.calls[-1] == ("stop",)
    assert sink.closed


def test_a_motor_failing_to_stop_still_stops_the_other_and_closes_the_sink():
    sink, idle = RecordingSink(), RecordingMotor()
    with pytest.raises(OSError):
        talk([ScriptedSource([])], ticks=1, sink=sink, mouth=MotorThatFailsToStop(), idle=idle)
    assert idle.calls[-1] == ("stop",)
    assert sink.closed


@pytest.mark.parametrize("overrides", [{"open_max_v": 13.0}, {"close_v": 12.5}])
def test_voltages_beyond_the_supply_are_rejected_before_the_mouth_moves(overrides):
    sink, mouth = RecordingSink(), RecordingMotor()
    with pytest.raises(ValueError):
        talk([ScriptedSource([LOUD])], ticks=1, settings=TalkSettings(**overrides), sink=sink, mouth=mouth)
    assert drives(mouth) == []
    assert sink.frames == []
    assert sink.closed
```

- [ ] **Step 4: Run to see them fail**

Run: `.venv/bin/pytest tests/test_talk_loop.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.talk_loop'`.

- [ ] **Step 5: Implement `motor_test/talk_loop.py`**

```python
"""Jack's talk loop: plays the voices, measures their loudness and moves the mouth to match.

The sound card's blocking write paces the loop at one 20 ms frame per tick, so audio,
mouth and watchdog all run on the same clock. See "Talk loop" in SPEC.md.
"""

from collections import deque
from collections.abc import Callable, Sequence

from motor_test.attempt_all import attempt_all
from motor_test.envelope import EnvelopeFollower, rms_dbfs
from motor_test.lip_sync import MouthController
from motor_test.pcm import TICK_S, TICKS_PER_SECOND, mix, silence
from motor_test.ports import AudioSink, MotorOutput, VoiceSource
from motor_test.ramp import volts_to_count
from motor_test.talk_settings import TalkSettings


def run_talk_loop(
    sources: Sequence[VoiceSource],
    sink: AudioSink,
    mouth: MotorOutput,
    idle_motors: Sequence[MotorOutput],
    settings: TalkSettings,
    supply_volts: float,
    on_second: Callable[[], None],
    until: Callable[[], bool] = lambda: False,
) -> None:
    """Play and lip-sync every source until `until()` is true (never, for the app).

    Each tick mixes the next frame from every source (silence if none are sounding),
    drives the mouth from its smoothed loudness, then plays the frame `mouth_lead_ticks`
    ticks later so the mouth can run ahead of the sound. `on_second` runs once per
    second of audio. Every motor is stopped and the sink closed on the way out, whatever
    the reason, and a failure in one of those does not skip the others.
    """
    envelope = EnvelopeFollower(settings.attack_s, settings.release_s, TICK_S)
    controller = MouthController(settings)
    delayed_audio = deque(silence() for _ in range(settings.mouth_lead_ticks))
    ticks = 0
    try:
        _check_supply(settings, supply_volts)
        for motor in idle_motors:
            motor.drive(0)
        while not until():
            frames = [frame for source in sources for frame in source.take_frames()]
            frame = mix(frames) if frames else silence()
            volts = controller.update(envelope.update(rms_dbfs(frame)))
            mouth.drive(volts_to_count(volts, supply_volts))
            delayed_audio.append(frame)
            sink.write(delayed_audio.popleft())
            ticks += 1
            if ticks % TICKS_PER_SECOND == 0:
                on_second()
    finally:
        attempt_all([mouth.stop, *(motor.stop for motor in idle_motors), sink.close])


def _check_supply(settings: TalkSettings, supply_volts: float) -> None:
    for name in ("open_max_v", "close_v"):
        if getattr(settings, name) > supply_volts:
            raise ValueError(f"{name} ({getattr(settings, name)} V) exceeds the {supply_volts} V supply")
```

- [ ] **Step 6: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_talk_loop.py -v && .venv/bin/pytest`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/ports.py motor_test/talk_loop.py tests/fakes.py tests/test_talk_loop.py tests/test_smoke_test.py tests/test_calibration.py
git commit -m "Add the talk loop: mix voice sources, drive the mouth from their loudness, play in time with the sound card"
```

---

### Task 6: ALSA sink adapter

**Files:**
- Create: `motor_test/alsa_sink.py`
- Test: `tests/test_alsa_sink.py`

**Interfaces:**
- Consumes: `pcm.FRAME_SAMPLES`, `pcm.SAMPLE_RATE_HZ`.
- Produces: `motor_test.alsa_sink.AlsaSink(pcm, error_type: type[Exception], log: Callable[[str], None])` (an `AudioSink`, with `.underruns: int`); `open_alsa_sink(device: str, periods: int, log: Callable[[str], None]) -> AlsaSink`.

pyalsaaudio 0.10.0 facts (from its source, `alsaaudio.c` `alsapcm_write`): `write()` re-prepares a device left in the XRUN state before writing, and raises `alsaaudio.ALSAAudioError` with message `"<snd_strerror> [<card>]"` when the write itself fails; an underrun during the write is `-EPIPE`, whose `snd_strerror` text is `"Broken pipe"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_alsa_sink.py`:

```python
import pytest

from motor_test.alsa_sink import AlsaSink
from tests.audio import constant_frame


class FakeAlsaError(Exception):
    pass


class ScriptedPcm:
    """Stand-in for alsaaudio.PCM: write number n raises errors[n-1] when that entry isn't None."""

    def __init__(self, errors=()):
        self.written = []
        self.closed = False
        self._errors = list(errors)

    def write(self, data):
        error = self._errors.pop(0) if self._errors else None
        if error is not None:
            raise error
        self.written.append(data)
        return len(data) // 2

    def close(self):
        self.closed = True


def make_sink(pcm):
    log = []
    return AlsaSink(pcm, FakeAlsaError, log.append), log


def test_frames_go_to_the_device():
    pcm = ScriptedPcm()
    sink, log = make_sink(pcm)
    sink.write(constant_frame(5))
    assert pcm.written == [constant_frame(5)]
    assert log == []


def test_an_underrun_is_counted_logged_and_ridden_through():
    pcm = ScriptedPcm([FakeAlsaError("Broken pipe [Headphones]"), None])
    sink, log = make_sink(pcm)
    sink.write(constant_frame(1))
    sink.write(constant_frame(2))
    assert sink.underruns == 1
    assert log == ["Audio underrun #1; carrying on"]
    assert pcm.written == [constant_frame(2)]


def test_repeated_underruns_log_the_first_and_every_hundredth():
    pcm = ScriptedPcm([FakeAlsaError("Broken pipe [Headphones]")] * 200)
    sink, log = make_sink(pcm)
    for _ in range(200):
        sink.write(constant_frame(0))
    assert log == ["Audio underrun #1; carrying on", "Audio underrun #100; carrying on", "Audio underrun #200; carrying on"]


def test_other_device_errors_propagate():
    sink, _ = make_sink(ScriptedPcm([FakeAlsaError("No such device [Headphones]")]))
    with pytest.raises(FakeAlsaError):
        sink.write(constant_frame(0))


def test_close_closes_the_device():
    pcm = ScriptedPcm()
    sink, _ = make_sink(pcm)
    sink.close()
    assert pcm.closed
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest tests/test_alsa_sink.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.alsa_sink'`.

- [ ] **Step 3: Implement `motor_test/alsa_sink.py`**

```python
"""AudioSink on an ALSA playback device (the Pi's 3.5 mm jack) through python3-alsaaudio."""

from collections.abc import Callable

from motor_test.pcm import FRAME_SAMPLES, SAMPLE_RATE_HZ

# snd_strerror(-EPIPE): how alsaaudio reports that the device ran dry (an underrun).
UNDERRUN_MESSAGE = "Broken pipe"
# Log the first underrun and then every this-many, so a struggling loop can't flood the journal.
UNDERRUN_LOG_EVERY = 100


class AlsaSink:
    """Plays frames on an open alsaaudio PCM; underruns are logged and ridden through."""

    def __init__(self, pcm, error_type: type[Exception], log: Callable[[str], None]):
        self._pcm = pcm
        self._error_type = error_type
        self._log = log
        self.underruns = 0

    def write(self, frame: bytes) -> None:
        try:
            self._pcm.write(frame)
        except self._error_type as error:
            if not str(error).startswith(UNDERRUN_MESSAGE):
                raise
            self.underruns += 1
            if self.underruns == 1 or self.underruns % UNDERRUN_LOG_EVERY == 0:
                self._log(f"Audio underrun #{self.underruns}; carrying on")

    def close(self) -> None:
        self._pcm.close()


def open_alsa_sink(device: str, periods: int, log: Callable[[str], None]) -> AlsaSink:
    """Open `device` for blocking mono 16-bit playback, one talk-loop frame per ALSA period."""
    import alsaaudio  # Only on the Pi (python3-alsaaudio); tests use AlsaSink with a stand-in PCM.

    pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_PLAYBACK,
        mode=alsaaudio.PCM_NORMAL,
        rate=SAMPLE_RATE_HZ,
        channels=1,
        format=alsaaudio.PCM_FORMAT_S16_LE,
        periodsize=FRAME_SAMPLES,
        periods=periods,
        device=device,
    )
    return AlsaSink(pcm, alsaaudio.ALSAAudioError, log)
```

- [ ] **Step 4: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_alsa_sink.py -v && .venv/bin/pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/alsa_sink.py tests/test_alsa_sink.py
git commit -m "Add an ALSA audio sink that rides through underruns"
```

---

### Task 7: WAV source, shared service guard and the lip-sync tuning tool

**Files:**
- Create: `motor_test/service_guard.py`, `motor_test/wav_source.py`, `lipsync_wav.py`
- Modify: `calibrate.py` (use `service_guard`)
- Test: `tests/test_service_guard.py`, `tests/test_wav_source.py`, `tests/test_lipsync_wav.py`

**Interfaces:**
- Consumes: `TalkSettings`, `run_talk_loop`, `open_alsa_sink`, `pcm` constants; from `main`: `ALSA_DEVICE`, `ALSA_PERIODS`, `I2C_BUS`, `PCA9685_ADDRESS`, `PWM_FREQ_HZ`, `SUPPLY_VOLTS` (Task 9 adds `ALSA_DEVICE` and `ALSA_PERIODS`; this task adds them to `main.py` first, see Step 7).
- Produces:
  - `motor_test.service_guard`: `APP_SERVICE = "jack.service"`, `STOP_APP_FIRST: str`, `app_is_running() -> bool`.
  - `motor_test.wav_source.WavSource(path: str | Path)` with `.take_frames() -> list[bytes]`, `.finished: bool`.
  - `lipsync_wav`: `build_parser() -> argparse.ArgumentParser`, `settings_from_args(args) -> TalkSettings`, `after_tail(source, tail_ticks: int) -> Callable[[], bool]`, `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing service-guard tests**

`tests/test_service_guard.py`:

```python
import os

import pytest

from motor_test.service_guard import APP_SERVICE, STOP_APP_FIRST, app_is_running


@pytest.fixture
def fake_systemctl(tmp_path, monkeypatch):
    """Put a `systemctl` on PATH that records its arguments and exits with the chosen status."""

    def install(exit_code):
        args_log = tmp_path / "systemctl-args"
        script = tmp_path / "systemctl"
        script.write_text(f'#!/bin/sh\necho "$@" > "{args_log}"\nexit {exit_code}\n')
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        return args_log

    return install


def test_running_app_is_detected(fake_systemctl):
    args_log = fake_systemctl(0)
    assert app_is_running()
    assert args_log.read_text().strip() == "is-active --quiet jack.service"


def test_stopped_app_is_detected(fake_systemctl):
    fake_systemctl(3)
    assert not app_is_running()


def test_message_says_how_to_stop_the_app():
    assert APP_SERVICE in STOP_APP_FIRST
    assert "sudo systemctl stop jack" in STOP_APP_FIRST
```

- [ ] **Step 2: Run to see them fail, then implement `motor_test/service_guard.py`**

Run: `.venv/bin/pytest tests/test_service_guard.py -v` → `ModuleNotFoundError`.

```python
"""Keeps hand-run tools off the HAT and the sound card while the jack service is using them."""

import subprocess

APP_SERVICE = "jack.service"
STOP_APP_FIRST = f"{APP_SERVICE} is running and driving the HAT. Stop it first: sudo systemctl stop jack"


def app_is_running() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", APP_SERVICE], check=False).returncode == 0
```

Run again: 3 passed.

- [ ] **Step 3: Point `calibrate.py` at the shared guard**

In `calibrate.py`: delete `import subprocess`, the `APP_SERVICE` constant and the local `app_is_running` function; add `from motor_test.service_guard import STOP_APP_FIRST, app_is_running`; replace the refusal print with `print(STOP_APP_FIRST, file=sys.stderr)`.

Run: `.venv/bin/pytest && .venv/bin/python -c "import calibrate"`
Expected: all pass; the import succeeds.

- [ ] **Step 4: Write the failing WAV-source tests**

`tests/test_wav_source.py`:

```python
import wave

import pytest

from motor_test.pcm import FRAME_BYTES
from motor_test.wav_source import WavSource
from tests.audio import constant_frame


def write_wav(path, pcm, channels=1, sample_width=2, rate=48000):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(sample_width)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return path


def test_plays_one_frame_per_tick_padding_the_last_then_finishes(tmp_path):
    tail = constant_frame(3)[: FRAME_BYTES // 2]
    source = WavSource(write_wav(tmp_path / "voice.wav", constant_frame(1) + constant_frame(2) + tail))
    assert source.take_frames() == [constant_frame(1)]
    assert source.take_frames() == [constant_frame(2)]
    assert source.take_frames() == [tail + bytes(FRAME_BYTES // 2)]
    assert not source.finished
    assert source.take_frames() == []
    assert source.finished
    assert source.take_frames() == []


@pytest.mark.parametrize(
    "channels, sample_width, rate",
    [(2, 2, 48000), (1, 2, 44100), (1, 1, 48000)],
)
def test_wrong_format_is_rejected_with_how_to_convert(tmp_path, channels, sample_width, rate):
    path = write_wav(tmp_path / "voice.wav", bytes(channels * sample_width * 10), channels, sample_width, rate)
    with pytest.raises(ValueError, match="mono 16-bit 48000 Hz") as error:
        WavSource(path)
    assert "afconvert" in str(error.value)
```

- [ ] **Step 5: Run to see them fail, then implement `motor_test/wav_source.py`**

Run: `.venv/bin/pytest tests/test_wav_source.py -v` → `ModuleNotFoundError`.

```python
"""VoiceSource that plays one WAV file, a frame per tick, for tuning the mouth without Mumble."""

import wave
from pathlib import Path

from motor_test.pcm import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_BYTES, SAMPLE_RATE_HZ


class WavSource:
    def __init__(self, path: str | Path):
        self._wav = wave.open(str(path), "rb")
        found = (self._wav.getnchannels(), self._wav.getsampwidth(), self._wav.getframerate())
        if found != (1, SAMPLE_BYTES, SAMPLE_RATE_HZ):
            self._wav.close()
            channels, width, rate = found
            raise ValueError(
                f"{path} is {channels}-channel {8 * width}-bit {rate} Hz; it must be mono 16-bit "
                f"{SAMPLE_RATE_HZ} Hz. On a Mac: afconvert -f WAVE -d LEI16@{SAMPLE_RATE_HZ} -c 1 IN OUT.wav"
            )
        self.finished = False

    def take_frames(self) -> list[bytes]:
        """The file's next frame (the last one padded with silence), then nothing once it has ended."""
        if self.finished:
            return []
        data = self._wav.readframes(FRAME_SAMPLES)
        if not data:
            self.finished = True
            self._wav.close()
            return []
        return [data.ljust(FRAME_BYTES, b"\0")]
```

Run again: all pass.

- [ ] **Step 6: Write the failing tool tests**

`tests/test_lipsync_wav.py`:

```python
import wave
from types import SimpleNamespace

import pytest

import lipsync_wav
from motor_test.talk_settings import TalkSettings


def test_defaults_are_the_starting_settings():
    args = lipsync_wav.build_parser().parse_args(["voice.wav"])
    assert lipsync_wav.settings_from_args(args) == TalkSettings()
    assert args.wav == "voice.wav"


def test_every_setting_can_be_overridden_from_the_command_line():
    args = lipsync_wav.build_parser().parse_args(
        ["voice.wav", "--gate-open-db", "-30", "--release-s", "0.1", "--mouth-lead-ms", "40"]
    )
    settings = lipsync_wav.settings_from_args(args)
    assert (settings.gate_open_db, settings.release_s, settings.mouth_lead_ms) == (-30.0, 0.1, 40.0)


def test_refuses_while_the_app_is_running(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: True)
    assert lipsync_wav.main(["voice.wav"]) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_impossible_override_is_rejected_before_touching_hardware(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main(["voice.wav", "--open-min-v", "3", "--open-max-v", "2"]) == 2
    assert "open_max_v" in capsys.readouterr().err


def test_wrong_format_wav_is_rejected_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(bytes(40))
    assert lipsync_wav.main([str(path)]) == 2
    assert "mono 16-bit 48000 Hz" in capsys.readouterr().err


def test_runs_on_for_the_tail_after_the_wav_ends():
    source = SimpleNamespace(finished=False)
    until = lipsync_wav.after_tail(source, tail_ticks=2)
    assert not until()
    source.finished = True
    assert [until(), until(), until()] == [False, False, True]
```

- [ ] **Step 7: Run to see them fail, then add the ALSA constants to `main.py` and implement `lipsync_wav.py`**

Run: `.venv/bin/pytest tests/test_lipsync_wav.py -v` → `ModuleNotFoundError: No module named 'lipsync_wav'`.

In `main.py`, after `SUPPLY_VOLTS = 12.0`, add (use the device and periods Task 1 recorded if they differ):

```python
# The 3.5 mm jack; plughw converts formats if the card needs it (Task 1 of the talk plan checked it).
ALSA_DEVICE = "plughw:CARD=Headphones,DEV=0"
ALSA_PERIODS = 4
```

`lipsync_wav.py`:

```python
"""Tune Jack's lip sync by eye: plays a WAV through the talk loop on the Pi, every setting overridable.

Stop the app first so the two programs don't fight over the HAT and the sound card:
    sudo systemctl stop jack
    /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py voice.wav --gate-open-db -30 --release-s 0.1
The WAV must be mono 16-bit 48 kHz. Settings are in motor_test/talk_settings.py.
"""

import argparse
import dataclasses
import sys
from collections.abc import Callable

from smbus2 import SMBus

from main import ALSA_DEVICE, ALSA_PERIODS, I2C_BUS, PCA9685_ADDRESS, PWM_FREQ_HZ, SUPPLY_VOLTS
from motor_test.alsa_sink import open_alsa_sink
from motor_test.pca9685 import Pca9685
from motor_test.pcm import TICKS_PER_SECOND
from motor_test.service_guard import STOP_APP_FIRST, app_is_running
from motor_test.talk_loop import run_talk_loop
from motor_test.talk_settings import TalkSettings
from motor_test.tb6612_motor import MOTOR_A, MOTOR_B, Tb6612Motor
from motor_test.wav_source import WavSource

# Keep running this long after the WAV ends so the mouth closes before the motors brake.
TAIL_TICKS = TICKS_PER_SECOND


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Play a WAV through Jack's talk loop to tune the lip sync.")
    parser.add_argument("wav", help="mono 16-bit 48 kHz WAV file")
    defaults = TalkSettings()
    for field in dataclasses.fields(TalkSettings):
        default = getattr(defaults, field.name)
        parser.add_argument(f"--{field.name.replace('_', '-')}", type=float, default=default, help=f"default {default}")
    return parser


def settings_from_args(args: argparse.Namespace) -> TalkSettings:
    return TalkSettings(**{field.name: getattr(args, field.name) for field in dataclasses.fields(TalkSettings)})


def after_tail(source, tail_ticks: int) -> Callable[[], bool]:
    """An `until` check for the talk loop: true once `tail_ticks` ticks have run after the source finished."""
    remaining = tail_ticks

    def done() -> bool:
        nonlocal remaining
        if source.finished:
            if remaining == 0:
                return True
            remaining -= 1
        return False

    return done


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if app_is_running():
        print(STOP_APP_FIRST, file=sys.stderr)
        return 1
    try:
        settings = settings_from_args(args)
        source = WavSource(args.wav)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)
        return 2
    print(f"Playing {args.wav} on {ALSA_DEVICE} with {settings}")
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        try:
            run_talk_loop(
                [source],
                sink,
                Tb6612Motor(chip, MOTOR_B),
                [Tb6612Motor(chip, MOTOR_A)],
                settings,
                SUPPLY_VOLTS,
                lambda: None,
                after_tail(source, TAIL_TICKS),
            )
        except KeyboardInterrupt:
            print("\nstopped; motors braked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Note the order in `main`: the service check and all validation happen before `SMBus` is opened, so a rejected run never touches the HAT. `OSError` covers a missing WAV file.

- [ ] **Step 8: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_service_guard.py tests/test_wav_source.py tests/test_lipsync_wav.py -v && .venv/bin/pytest`
Expected: all pass. The `after_tail` test sequence: `[False, False, True]` after finishing with `tail_ticks=2` (two more ticks run, then stop).

- [ ] **Step 9: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/service_guard.py motor_test/wav_source.py lipsync_wav.py calibrate.py main.py tests/test_service_guard.py tests/test_wav_source.py tests/test_lipsync_wav.py
git commit -m "Add lipsync_wav.py to tune the mouth from a WAV, sharing calibrate's running-app check"
```

---

### Task 8: Mumble voice adapter

**Files:**
- Create: `motor_test/mumble_voice.py`
- Test: `tests/test_mumble_voice.py`

**Interfaces:**
- Consumes: `FrameQueue`, `pcm.TICK_S`.
- Produces: `motor_test.mumble_voice.MumbleVoice(max_backlog_frames: int, log: Callable[[str], None])` (a `VoiceSource`) with `on_sound(user, chunk)`, `on_connected()`, `on_disconnected()`; `connect_mumble(voice: MumbleVoice, host: str, port: int, user: str, password: str)` returning the started pymumble client.

pymumble facts (source at `a560e60`): the sound callback runs on pymumble's thread as `(user, chunk)`; `user["session"]` is the talker's session id and `user["name"]` their name; `chunk.pcm` is mono 16-bit 48 kHz bytes. `PYMUMBLE_CLBK_CONNECTED` and `PYMUMBLE_CLBK_DISCONNECTED` are called with no arguments. The client is a non-daemon `threading.Thread`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mumble_voice.py`:

```python
from types import SimpleNamespace

from motor_test.mumble_voice import MumbleVoice
from motor_test.pcm import FRAME_BYTES
from tests.audio import constant_frame

BOSS = {"session": 1, "name": "Boss"}
GUEST = {"session": 2, "name": "Guest"}


def chunk(pcm):
    return SimpleNamespace(pcm=pcm)


def voice(max_backlog_frames=10):
    log = []
    return MumbleVoice(max_backlog_frames, log.append), log


def test_nobody_talking_gives_no_frames():
    source, _ = voice()
    assert source.take_frames() == []


def test_a_talker_is_heard_one_frame_per_tick():
    source, _ = voice()
    source.on_sound(BOSS, chunk(constant_frame(1) + constant_frame(2)))
    assert [source.take_frames(), source.take_frames(), source.take_frames()] == [
        [constant_frame(1)],
        [constant_frame(2)],
        [],
    ]


def test_talkers_are_kept_apart_so_the_loop_can_mix_them():
    source, _ = voice()
    source.on_sound(BOSS, chunk(constant_frame(1)))
    source.on_sound(GUEST, chunk(constant_frame(2)))
    assert source.take_frames() == [constant_frame(1), constant_frame(2)]


def test_chunks_are_recut_into_whole_frames():
    source, _ = voice()
    data = constant_frame(4) * 2
    source.on_sound(BOSS, chunk(data[: FRAME_BYTES // 2]))
    assert source.take_frames() == []
    source.on_sound(BOSS, chunk(data[FRAME_BYTES // 2 :]))
    assert source.take_frames() == [constant_frame(4)]


def test_backlog_is_dropped_and_logged():
    source, log = voice(max_backlog_frames=2)
    source.on_sound(BOSS, chunk(b"".join(constant_frame(value) for value in range(5))))
    assert log == ["Dropped 60 ms of Boss's voice to keep up"]
    assert [source.take_frames(), source.take_frames()] == [[constant_frame(3)], [constant_frame(4)]]


def test_connection_changes_are_logged():
    source, log = voice()
    source.on_connected()
    source.on_disconnected()
    assert log == ["Connected to Mumble", "Disconnected from Mumble; pymumble retries every 10 s"]
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest tests/test_mumble_voice.py -v`
Expected: `ModuleNotFoundError: No module named 'motor_test.mumble_voice'`.

- [ ] **Step 3: Implement `motor_test/mumble_voice.py`**

```python
"""VoiceSource for a Mumble channel: Jack joins as a bot and hears everyone talking there.

pymumble delivers each talker's decoded audio on its own thread; every talker gets a
FrameQueue so the talk loop can take one frame per talker per tick and mix them.
"""

import threading
from collections.abc import Callable

from motor_test.frame_queue import FrameQueue
from motor_test.pcm import TICK_S


class MumbleVoice:
    def __init__(self, max_backlog_frames: int, log: Callable[[str], None]):
        self._max_backlog_frames = max_backlog_frames
        self._log = log
        self._queues: dict[int, FrameQueue] = {}
        self._lock = threading.Lock()

    def on_sound(self, user, chunk) -> None:
        """pymumble's sound-received callback; runs on pymumble's thread."""
        with self._lock:
            queue = self._queues.setdefault(user["session"], FrameQueue(self._max_backlog_frames))
        dropped = queue.put(chunk.pcm)
        if dropped:
            self._log(f"Dropped {round(dropped * TICK_S * 1000)} ms of {user['name']}'s voice to keep up")

    def on_connected(self) -> None:
        self._log("Connected to Mumble")

    def on_disconnected(self) -> None:
        self._log("Disconnected from Mumble; pymumble retries every 10 s")

    def take_frames(self) -> list[bytes]:
        with self._lock:
            queues = list(self._queues.values())
        return [frame for frame in (queue.take() for queue in queues) if frame is not None]


def connect_mumble(voice: MumbleVoice, host: str, port: int, user: str, password: str):
    """Start a pymumble bot that feeds `voice` and reconnects on its own; returns the client."""
    import pymumble_py3 as pymumble  # Only in the Pi's venv; tests drive MumbleVoice directly.
    from pymumble_py3.constants import (
        PYMUMBLE_CLBK_CONNECTED,
        PYMUMBLE_CLBK_DISCONNECTED,
        PYMUMBLE_CLBK_SOUNDRECEIVED,
    )

    client = pymumble.Mumble(host, user, port=port, password=password, reconnect=True, client_type=1)
    # pymumble's thread is not a daemon; without this the process could not exit on SIGTERM or a crash.
    client.daemon = True
    client.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, voice.on_sound)
    client.callbacks.set_callback(PYMUMBLE_CLBK_CONNECTED, voice.on_connected)
    client.callbacks.set_callback(PYMUMBLE_CLBK_DISCONNECTED, voice.on_disconnected)
    client.set_receive_sound(True)
    client.start()
    return client
```

- [ ] **Step 4: Run the new and full suites**

Run: `.venv/bin/pytest tests/test_mumble_voice.py -v && .venv/bin/pytest`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git status --short && git log --oneline -3
git add motor_test/mumble_voice.py tests/test_mumble_voice.py
git commit -m "Add the Mumble voice source: one frame queue per talker, fed by a reconnecting pymumble bot"
```

---

### Task 9: Talk from `main.py`

**Files:**
- Modify: `main.py`
- Test: `tests/test_main.py` (add)

**Interfaces:**
- Consumes: everything above; `systemd_notify.notify`.
- Produces: `main.MUMBLE_PASSWORD_ENV = "JACK_MUMBLE_PASSWORD"`, `main.mumble_password(environ: Mapping[str, str] = os.environ) -> str`, and `main.main()` running the talk loop. `speaking_cycle`, `demo_cycle`, `MOUTH_DEMO` stay (Boss: keep them).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_main.py`:

```python
def test_mumble_password_comes_from_the_environment():
    assert main.mumble_password({"JACK_MUMBLE_PASSWORD": "s3cret"}) == "s3cret"


@pytest.mark.parametrize("environ", [{}, {"JACK_MUMBLE_PASSWORD": ""}])
def test_missing_mumble_password_exits_with_where_to_set_it(environ):
    with pytest.raises(SystemExit) as exit_info:
        main.mumble_password(environ)
    assert "JACK_MUMBLE_PASSWORD" in str(exit_info.value.code)
    assert "/etc/jack/jack.env" in str(exit_info.value.code)
```

- [ ] **Step 2: Run to see them fail**

Run: `.venv/bin/pytest tests/test_main.py -v`
Expected: `AttributeError: module 'main' has no attribute 'mumble_password'`.

- [ ] **Step 3: Rewire `main.py`**

Change the module docstring to:

```python
"""Jack talks: Boss's voice from Mumble plays on the 3.5 mm jack and the mouth (motor B) moves with it; motor A stays off."""
```

Add imports (keep the existing ones; `speaking_cycle`/`demo_cycle` still use them):

```python
import os
from collections.abc import Mapping

from motor_test.alsa_sink import open_alsa_sink
from motor_test.mumble_voice import MumbleVoice, connect_mumble
from motor_test.talk_loop import run_talk_loop
from motor_test.talk_settings import TalkSettings
```

After the `ALSA_PERIODS` constant add:

```python
# mumble-server runs on the Pi itself; Boss's client connects to 10.10.0.54:64738.
MUMBLE_HOST = "127.0.0.1"
MUMBLE_PORT = 64738
MUMBLE_USER = "Jack"
MUMBLE_PASSWORD_ENV = "JACK_MUMBLE_PASSWORD"
```

Change `ping_watchdog`'s docstring to `"""Tell systemd's watchdog the app is still running its loop."""`. Add:

```python
def mumble_password(environ: Mapping[str, str] = os.environ) -> str:
    """The Mumble server password, which systemd loads from /etc/jack/jack.env."""
    password = environ.get(MUMBLE_PASSWORD_ENV, "")
    if not password:
        raise SystemExit(
            f"{MUMBLE_PASSWORD_ENV} is empty or unset. Put the Mumble server password in "
            "/etc/jack/jack.env, then: sudo systemctl restart jack"
        )
    return password
```

Replace `main()` with:

```python
def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    settings = TalkSettings()
    voice = MumbleVoice(settings.max_backlog_frames, print)
    connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, mumble_password())
    with SMBus(I2C_BUS) as bus:
        chip = Pca9685(bus, PCA9685_ADDRESS, PWM_FREQ_HZ)
        sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
        print(f"Talking: Mumble voice on {ALSA_DEVICE}, mouth on motor B, {SUPPLY_VOLTS} V supply, motor A off")
        notify("READY=1")
        run_talk_loop(
            [voice],
            sink,
            Tb6612Motor(chip, MOTOR_B),
            [Tb6612Motor(chip, MOTOR_A)],
            settings,
            SUPPLY_VOLTS,
            ping_watchdog,
        )
```

Remove imports that are now unused only if nothing else in `main.py` uses them (`run_generated_loop` and `time` become unused by `main()`; check with `grep -n "run_generated_loop\|time\." main.py` and remove only unused ones).

- [ ] **Step 4: Run the full suite and an import check**

Run: `.venv/bin/pytest && .venv/bin/python -c "import main, lipsync_wav, calibrate"`
Expected: all pass; imports succeed on the Mac (pymumble and alsaaudio are not imported at module load).

- [ ] **Step 5: Commit**

```bash
git status --short && git log --oneline -3
git add main.py tests/test_main.py
git commit -m "Run the talk loop from main: Mumble voice in, lip-synced mouth, watchdog every second"
```

---

### Task 10: Deployment

**Files:**
- Create: `requirements-pi.txt`
- Modify: `deploy/jack.service`, `deploy/jack-update.sh`, `deploy/install.sh`, `README.md`
- Test: `tests/test_units.py`, `tests/test_jack_update.py`, `tests/test_install.py` (new)

**Interfaces:**
- Consumes: nothing from code tasks.
- Produces: `/opt/jack-venv` with pymumble; `jack.service` runs the venv's Python with `EnvironmentFile=/etc/jack/jack.env`; `jack-update.sh` honours `JACK_VENV_DIR` (default `/opt/jack-venv`).

- [ ] **Step 1: Write the failing unit-file tests**

In `tests/test_units.py`, change `test_app_runs_as_jack_at_boot_without_login`'s group assertion to `== "i2c audio"`, and add:

```python
def test_app_runs_on_the_venv_python_with_its_secrets_file():
    unit = load_unit()
    assert unit["Service"]["ExecStart"] == "/opt/jack-venv/bin/python /opt/jack/main.py"
    assert unit["Service"]["EnvironmentFile"] == "/etc/jack/jack.env"
```

- [ ] **Step 2: Run to see them fail, then update `deploy/jack.service`**

Run: `.venv/bin/pytest tests/test_units.py -v` → 2 failures.

In `deploy/jack.service`: `SupplementaryGroups=i2c audio`, add `EnvironmentFile=/etc/jack/jack.env` after `Environment=PYTHONUNBUFFERED=1`, and `ExecStart=/opt/jack-venv/bin/python /opt/jack/main.py`.

Run again: all pass.

- [ ] **Step 3: Write the failing updater tests**

In `tests/test_jack_update.py`:
- In the `deployment` fixture, before the initial commit add `(work / "requirements-pi.txt").write_text("pymumble @ git+https://example.invalid/pymumble@one\n")`.
- Also in the fixture, create a fake venv pip and export its location:

```python
    venv_dir = tmp_path / "venv"
    (venv_dir / "bin").mkdir(parents=True)
    pip_log = tmp_path / "pip.log"
    pip = venv_dir / "bin" / "pip"
    # Records its arguments and the requirements it was given; PIP_EXIT makes it fail.
    pip.write_text(
        '#!/bin/sh\n'
        'echo "$@" >> "$PIP_LOG"\n'
        'while [ $# -gt 0 ]; do if [ "$1" = "-r" ]; then cat "$2" >> "$PIP_LOG"; fi; shift; done\n'
        'exit "${PIP_EXIT:-0}"\n'
    )
    pip.chmod(0o755)
```

and add `"JACK_VENV_DIR": str(venv_dir), "PIP_LOG": str(pip_log),` to `env`, a `pip_log: Path` field to `Deployment` (constructed with `pip_log`), and this method:

```python
    def pip_runs(self):
        if not self.pip_log.exists():
            return []
        return self.pip_log.read_text().splitlines()
```

Add the tests:

```python
def test_changed_requirements_are_installed_before_the_restart(deployment):
    new_requirement = "pymumble @ git+https://example.invalid/pymumble@two"
    new_head = deployment.commit_and_push("requirements-pi.txt", new_requirement + "\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    runs = deployment.pip_runs()
    assert runs[0].startswith("install --quiet --no-deps -r ")
    assert runs[1] == new_requirement
    assert deployment.restarts() == ["try-restart jack.service"]


def test_unchanged_requirements_skip_pip(deployment):
    deployment.commit_and_push("main.py", "print('v2')\n")
    deployment.run_update()
    assert deployment.pip_runs() == []


def test_failed_requirements_install_leaves_the_old_deploy_running(deployment):
    before = git(deployment.checkout, "rev-parse", "HEAD")
    deployment.commit_and_push("requirements-pi.txt", "pymumble @ git+https://example.invalid/pymumble@broken\n")
    deployment.env["PIP_EXIT"] = "1"
    result = deployment.run_update()
    assert result.returncode != 0
    assert git(deployment.checkout, "rev-parse", "HEAD") == before
    assert deployment.restarts() == []
```

- [ ] **Step 4: Run to see them fail**

Run: `.venv/bin/pytest tests/test_jack_update.py -v`
Expected: the first and third new tests fail (pip never runs; the broken pin deploys anyway).

- [ ] **Step 5: Update `deploy/jack-update.sh`**

Inside `main()`, add `local venv_dir="${JACK_VENV_DIR:-/opt/jack-venv}"` after `local service=jack.service`, and insert between `echo "Deploying …"` and `git reset --hard …`:

```bash
  # Install a changed pymumble pin before switching code, so code never runs without it.
  # A failed install stops here (set -e): the old commit keeps running and the next poll retries.
  if ! git diff --quiet "$current" "$target" -- requirements-pi.txt; then
    local requirements="$repo_dir/.git/jack-requirements-pi.txt"
    git show "$target:requirements-pi.txt" > "$requirements"
    "$venv_dir/bin/pip" install --quiet --no-deps -r "$requirements"
  fi
```

Run: `.venv/bin/pytest tests/test_jack_update.py -v`
Expected: all pass (including the existing ones, e.g. the self-rewriting script test).

- [ ] **Step 6: Add `requirements-pi.txt`**

```text
# Installed on the Pi into /opt/jack-venv with --no-deps (deploy/install.sh, deploy/jack-update.sh);
# every other dependency comes from apt. Pinned to the commit the talk plan's spike proved on the Pi.
pymumble @ git+https://github.com/azlux/pymumble@a560e6013dfbccb3666ce8756e1ca6b790bf05c8
```

- [ ] **Step 7: Write the failing install-script test**

`tests/test_install.py`:

```python
import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"


def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_script_sets_up_talking():
    script = INSTALL.read_text()
    for package in ("mumble-server", "python3-alsaaudio", "python3-opuslib", "python3-protobuf", "python3-venv"):
        assert package in script
    assert "--system-site-packages" in script
    assert "--no-deps -r" in script
    assert "usermod --append --groups i2c,audio jack" in script
    assert "JACK_MUMBLE_PASSWORD=" in script
```

Run: `.venv/bin/pytest tests/test_install.py -v` → second test fails.

- [ ] **Step 8: Update `deploy/install.sh`**

Add `VENV_DIR=/opt/jack-venv` and `ENV_FILE=/etc/jack/jack.env` beside the other constants. Replace the `apt-get install` line with:

```bash
apt-get install -y git python3-smbus2 raspi-config mumble-server python3-alsaaudio python3-opuslib python3-protobuf python3-venv
```

Replace the `useradd` block with:

```bash
if ! id jack &>/dev/null; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin --groups i2c,audio jack
fi
# Existing installs predate the audio group.
usermod --append --groups i2c,audio jack
```

After the clone block, add:

```bash
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  python3 -m venv --system-site-packages "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --quiet --no-deps -r "$REPO_DIR/requirements-pi.txt"

# The Mumble password never lives in the (public) repo. Created empty once; never overwritten.
install -d -m 0750 -o root -g jack "$(dirname "$ENV_FILE")"
if [[ ! -f "$ENV_FILE" ]]; then
  install -m 0640 -o root -g jack /dev/null "$ENV_FILE"
  echo "JACK_MUMBLE_PASSWORD=" > "$ENV_FILE"
fi
```

After `systemctl daemon-reload`, add `systemctl try-restart jack.service` (so a running app picks up the new unit). Replace the final `echo` with:

```bash
echo "Installed. If /dev/i2c-1 is missing, reboot: sudo reboot"
echo "To talk: set serverpassword= in /etc/mumble/mumble-server.ini and JACK_MUMBLE_PASSWORD= in $ENV_FILE"
echo "to the same password, then: sudo systemctl restart mumble-server jack"
```

Run: `.venv/bin/pytest tests/test_install.py -v && .venv/bin/pytest`
Expected: all pass.

- [ ] **Step 9: Update `README.md`**

Add a "Talking" section after the existing calibration section:

````markdown
## Talking

Jack plays whatever is said in its Mumble server's root channel and moves the mouth with it.

1. Install the Mumble desktop client on your computer and connect to `10.10.0.54`,
   port `64738`, with the server password. Use push-to-talk.
2. The server password is set on the Pi in `/etc/mumble/mumble-server.ini` (`serverpassword=`) and
   `/etc/jack/jack.env` (`JACK_MUMBLE_PASSWORD=`); both must match. After changing them:
   `sudo systemctl restart mumble-server jack`.

### Tuning the lip sync

Record a WAV of speech, convert it to mono 16-bit 48 kHz on the Mac, copy it to the Pi and play it
through the same loop, overriding any setting from `motor_test/talk_settings.py`:

```bash
afconvert -f WAVE -d LEI16@48000 -c 1 speech.m4a speech.wav
scp speech.wav 10.10.0.54:/tmp/
ssh -t 10.10.0.54 'sudo systemctl stop jack && /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py /tmp/speech.wav --release-s 0.1'
```

Run with `--help` for every setting. When you like a set of values, make them the defaults in
`motor_test/talk_settings.py`, then `sudo systemctl start jack`.
````

- [ ] **Step 10: Commit**

```bash
git status --short && git log --oneline -3
git add requirements-pi.txt deploy/jack.service deploy/jack-update.sh deploy/install.sh README.md tests/test_units.py tests/test_jack_update.py tests/test_install.py
git commit -m "Deploy talking: pymumble venv, audio group, secrets file, pip on requirement changes"
```

---

### Task 11: Roll out and verify on the Pi (with Boss)

No mocks here: real Mumble, real sound card, real motor. Boss runs the `sudo` steps and decides the push.

**Files:**
- Modify: `SPEC.md` (record results), `motor_test/talk_settings.py` + `tests/test_talk_settings.py` (only if Boss picks new defaults)

- [ ] **Step 1: Push, with the app stopped so the old unit can't crash-loop on the new code**

Boss: `ssh -t 10.10.0.54 sudo systemctl stop jack`. Then, only on Boss's go-ahead, `git push origin main`. Wait for the deploy LEDs (within about 60 s); `try-restart` leaves the stopped app stopped.

- [ ] **Step 2: Boss re-runs the installer**

```bash
ssh -t 10.10.0.54 sudo bash /opt/jack/deploy/install.sh
```

Expected: completes; `journalctl -u jack -n 5` shows `JACK_MUMBLE_PASSWORD is empty or unset…` repeating every 5 s (the Review Focus #3 behavior on real systemd).

- [ ] **Step 3: Boss sets the real password in both places and restarts**

Edit `/etc/mumble/mumble-server.ini` (`serverpassword=`) and `/etc/jack/jack.env` (`JACK_MUMBLE_PASSWORD=`), then `sudo systemctl restart mumble-server jack`.
Expected in `journalctl -u jack -f`: `Connected to Mumble`, then `Talking: …`. No underrun lines while idle.

- [ ] **Step 4: Tune with a WAV**

Follow README "Tuning the lip sync". Boss watches the mouth and tries overrides (likely candidates: `--open-slew-v-per-s`, `--close-s`, `--release-s`, `--gate-open-db`, `--mouth-lead-ms`). Record the values Boss likes. If they differ from the defaults, update `TalkSettings` defaults and `test_starting_values_match_the_spec` together (TDD: change the test first, see it fail, change the default), and the "Mouth control" table in `SPEC.md`.

- [ ] **Step 5: End-to-end from Boss's Mumble client**

`sudo systemctl start jack` (if stopped). Boss talks. Check: voice audible, mouth in sync, no choppiness, delay acceptable at start and after 30 minutes.

- [ ] **Step 6: Self-recovery checks**

Run each and confirm the app is talking again with no human action:
- `sudo kill -9 $(systemctl show -p MainPID --value jack)` → restarted within about 5 s.
- `sudo kill -STOP $(systemctl show -p MainPID --value jack)` → watchdog kills and restarts within about 15 s.
- `sudo systemctl restart mumble-server` → `Disconnected…` then `Connected to Mumble` within about 10–15 s; voice resumes.
- `sudo reboot` → talking after boot.

- [ ] **Step 7: Record results and commit**

Add a `#### Rollout results (date)` subsection under "Talking" in `SPEC.md`: the tuned values, observed delay, reconnect times, and any underrun/drop log lines seen.

```bash
git status --short && git log --oneline -3
git add SPEC.md motor_test/talk_settings.py tests/test_talk_settings.py
git commit -m "Record Jack's talking rollout: tuned lip-sync settings and recovery checks"
```

Push only if Boss asks.
