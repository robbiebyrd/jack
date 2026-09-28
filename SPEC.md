# Jack: Raspberry Pi Motor HAT App with Git-Driven Deploys

## Purpose

The Raspberry Pi `jack` (10.10.0.54) runs `main.py` from this repository
as a boot-time system service. When `main` on GitHub changes, the Pi pulls
the new commit and restarts the app automatically. The initial `main.py` is
a hardware smoke test for the Waveshare Motor Driver HAT that proves the
whole chain works: push → Pi pulls → app restarts → motor output moves.

Next, Jack talks: Boss speaks into a Mumble client on a computer on the
same LAN, Jack plays the voice from its speaker, and the animatronic's
mouth (motor B) moves in sync with it (see "Talking").

## Hardware facts

Sources: Waveshare wiki (https://www.waveshare.com/wiki/Motor_Driver_HAT)
and its official sample code (`Motor_Driver_HAT_Code.7z`, `Raspberry Pi/python`).

- PWM controller: PCA9685 (12-bit), I2C bus 1, address `0x40`.
- Motor driver: TB6612FNG dual H-bridge.
- PCA9685 channel mapping:
  - Motor A: PWMA = 0, AIN1 = 1, AIN2 = 2
  - Motor B: PWMB = 5, BIN1 = 3, BIN2 = 4
- "Forward" in Waveshare's sample: IN1 = 0, IN2 = 1.
- Sample code PWM frequency: 50 Hz (wiki range: 40–1000 Hz).
- VIN supply on this build: **12 V**.
- Output voltage is PWM-switched VIN. Average voltage across the motor
  terminals ≈ VIN × duty (minus a small driver drop).

## Pi environment (observed)

- Debian GNU/Linux 13 (trixie), aarch64, Python 3.13.5.
- `python3-smbus2` 0.4.3 installed (apt).
- `git` not installed; `i2c-tools` not installed.
- I2C disabled (`#dtparam=i2c_arm=on` in `/boot/firmware/config.txt`).
- `sudo` requires a password, so Boss runs root setup steps.
- Pi 4, 4 cores, 8 GB RAM. Network is Wi-Fi (`wlan0`); `eth0` is down.
- Audio out: ALSA card 0, `bcm2835 Headphones` (the 3.5 mm jack); cards
  1 and 2 are HDMI. No audio input. No PipeWire, PulseAudio, mpv, ffmpeg,
  GStreamer or numpy installed.
- Trixie package candidates (checked 2026-09-28): `mumble-server`
  1.5.735-5+deb13u1, `python3-alsaaudio` 0.10.0, `python3-opuslib` 3.0.1,
  `python3-protobuf` 3.21.12. `pymumble` is not packaged.

## Smoke test behavior (`main.py`)

Purpose: find which voltages move the animatronic head's mouth (driven by
motor B) and how. The talk loop (see "Talking") replaces this demo in
`main.py`; the calibration table below stays the basis for its settings.

Mouth calibration, measured by Boss with `calibrate.py` (2026-09-28):

| Pose | How to reach it | Held without power? |
|---|---|---|
| Closed | +1 V for 0.25 s closes it from any pose; +0.5 V is enough from relaxed open | yes |
| Relaxed open | apply −2 V, then release: the mouth settles here | yes |
| Fully open | −6 V | no: only while −6 V is held |

Holding a voltage keeps the motor running against resistance (stalled), so
prefer the closed and relaxed-open poses and keep fully open short.

1. Drive both motor channels in lockstep in 50 ms steps. Each motor has
   its own signed profile; positive means forward (IN1 = 0, IN2 = 1, so
   terminal 1 is positive), negative means backward (IN1 = 1, IN2 = 0).
2. **Motor B (the mouth)** imitates speaking until control inputs (audio,
   DMX, websockets) are wired in. Each cycle plays a fresh random phrase from
   `motor_test/speech.py`, at most 4.5 s long:
   - 1–4 words of 1–3 syllables; 0.05–0.15 s between syllables, 0.1–0.3 s
     between words, and a 0.5–1.5 s pause after the phrase.
   - A syllable opens part way with −2 V for 0.15–0.4 s, then closes with
     +0.5 V for 0.15 s (the gentle close that works from relaxed open).
   - About 1 in 8 syllables is emphasised: a ramped full open (hold
     0.15–0.3 s), then +1 V for 0.25 s.
   - Every phrase ends closed. Durations are whole 50 ms steps.
   `MOUTH_DEMO` (close, rest 1.5 s, relax, rest 1.5 s, ramped full open for
   0.5 s; 4.5 s) remains available through `main.demo_cycle()` for checking
   the mechanism.
3. **Motor A** holds 0 V for the whole cycle.
4. Repeat the cycle back-to-back, with no pause, until the process is
   stopped.
5. On SIGTERM or any exception, **short-brake** both motors before exiting:
   duty 0, then IN1 = IN2 = high (the TB6612FNG shorts the motor leads).
   A failure braking one motor must not prevent braking the other.

## Calibration tool (`calibrate.py`)

For finding mouth positions interactively on the Pi, without a commit per try:

- Stop the app first (`sudo systemctl stop jack`); the tool refuses to run
  while `jack.service` is active, since both would drive the same chip.
- Run `python3 /opt/jack/calibrate.py` as a user in the `i2c` group. Each
  line `<volts> <seconds>` drives motor B (negative = backward), then
  short-brakes. `close`, `relax` and `open <seconds>` play the calibrated
  poses from `motor_test/mouth.py` (so `open` ramps too). Commands play in
  the app's 50 ms steps, so durations must be whole steps (0.8 works,
  0.33 is rejected). `q` or end of input quits.
- Volts must be within ±supply; seconds must be more than 0 and at most
  3 s, to limit stall heating against an end stop. Rejected lines never
  move the motor.
- The motor brakes after every move and on every exit: `q`, end of input,
  Ctrl-C, and I2C errors.
- Code: `motor_test/calibration.py` (`parse_command`, `run_calibration`,
  talking to motors only through `MotorOutput`) and `calibrate.py`
  (composition root reusing `main.py`'s hardware constants).

## Talking

Boss talks from a Mumble desktop client (push-to-talk) on a computer on the
same LAN; Jack plays the voice from its 3.5 mm jack and moves the mouth in
sync. One-way only: Jack has no microphone yet.

### Where the design comes from

Gemmy heads are commonly driven by a board like Blue Point Engineering's
Talking Skull DC Motor Controller V2
(http://www.bpesolutions.com/bpemanuals/TS.AssemblyGuide.pdf, schematic on
page 5). It is not programmable, but its circuit is a recipe:

| Stage | Parts | Function |
|---|---|---|
| Channel select | DPDT switch | Left or right stereo channel drives the mouth; the other passes to the speaker |
| Amplify, half-wave rectify | LM358 A: 10K input to ground, 100K feedback, 4.7K to ground | Non-inverting gain 1 + 100k/4.7k ≈ 22; single supply, so only the positive half passes; line level saturates it |
| Envelope | 1N4148, 0.1 µF, 10K to ground | Peak detector: near-instant attack, decay τ = 10k × 0.1 µF = 1 ms; the motor's inertia does the real smoothing |
| Gate and drive | IRF510 low-side MOSFET, motor on 4.5 V | Conducts only above the gate threshold (2–4 V per datasheet), a noise gate; one direction, a spring closes the mouth |
| Unused | LM358 B, 100K/100K divider | 4.5 V bias not connected to anything (ties off the spare op-amp) |

Jack does the same in software (level → gate → proportional drive) with
three improvements the board can't make: it closes the mouth actively
(our mouth holds its pose unpowered, see the calibration table), it limits
stall time, and it can run the mouth slightly ahead of the sound to hide
the motor's lag.

### Step 0: feasibility spike (throwaway)

`pymumble` (https://github.com/azlux/pymumble) last changed code in
November 2023 (Python 3.12 SSL fixes) and is not proven on Python 3.13 or
Mumble server 1.5. Before any product code, a throwaway script on the Pi
must show:

1. `pymumble` installs in a venv and connects to the local
   `mumble-server` as user `Jack`.
2. Boss's voice from the Mumble desktop client arrives as PCM, and the
   format (sample rate, sample width, channels, chunk size) is recorded.
3. That PCM plays out of ALSA card 0 through `python3-alsaaudio`, with a
   working ALSA period and buffer size recorded.
4. Whether pymumble's own reconnect recovers after
   `systemctl restart mumble-server`.
5. A rough mouth-to-speaker delay (Boss's judgement is fine; it's a
   sanity check, not a benchmark), at the start and again after 30
   minutes connected. pymumble's README warns that "the latency increase
   with connexion time" because it isn't asynchronous; the maintainer is
   looking for someone to take the project over.
6. Whether the voice sounds choppy over Jack's Wi-Fi (gaps would call for
   a small jitter buffer, which is not in this design).

Findings go into this spec; the script is not committed. If pymumble
can't do 1–3, stops recovering in 4, or drifts noticeably in 5, stop and
revisit the approach with Boss (the fallback considered was WebRTC with
`aiortc`, trixie 1.11.0).

pymumble facts from its source (commit `a560e60`): decoded audio is
mono 16-bit 48 kHz PCM; `reconnect=True` retries every 10 s
(`PYMUMBLE_CONNECTION_RETRY_INTERVAL`); the sound callback runs on
pymumble's own thread with `(user, SoundChunk)`, where `chunk.pcm` is the
bytes; its client thread is not a daemon, so the app must stop it or mark
it daemon or the process can't exit. License: GPLv3.

### Talk loop

One loop in `main.py`'s process, **paced by the sound card**: each tick
takes the next 20 ms frame of voice from a queue (or 20 ms of silence if
none arrived), writes it to ALSA (the blocking write is the clock),
updates the envelope, computes the mouth voltage and drives motor B.
Motor A holds 0 V.

- **Sources in:** the loop doesn't know where audio comes from. Each
  `VoiceSource` (the Mumble bot; in the tuning tool, a WAV file) puts
  decoded PCM on its own bounded queue, and every tick mixes the next frame
  of each source (summed, clipped to 16-bit). Several Mumble talkers mix
  the same way. Playing pre-recorded clips later is one more source, a
  new adapter, with no change to the loop, envelope or mouth control.
  The tuning tool exercises this path from day one.
- **Backlog:** if more than `MAX_BACKLOG_MS` (start: 200 ms) is queued,
  the oldest frames are dropped and one log line says how much. Latency
  therefore can't grow without bound on Wi-Fi bursts.
- **Mouth lead:** audio passes through a FIFO of `MOUTH_LEAD_MS` (whole
  ticks, start: 0) while the motor uses the undelayed frame, so the mouth
  runs that far ahead of the sound. The ALSA buffer already puts the mouth
  slightly ahead; this setting adds to it.
- **Watchdog:** `WATCHDOG=1` about once a second (every 50 ticks), whether
  or not anyone is talking, so a silent Jack is never mistaken for a hung
  one.

### Envelope (`envelope.py`)

Per 20 ms frame: RMS of the 16-bit samples in dBFS (full-scale = 32767;
digital silence reads as the floor, −90 dBFS). Then one-pole smoothing in
dB with separate time constants: `ATTACK_S` while rising, `RELEASE_S`
while falling (coefficient e^(−tick/τ)).

### Mouth control (`lip_sync.py`)

A pure, tick-based state machine: smoothed level in, signed volts for
motor B out.

| State | Motor B | Next |
|---|---|---|
| Closed | 0 V (short brake; the mouth holds closed unpowered) | level ≥ `GATE_OPEN_DB` → Open |
| Open | −(`OPEN_MIN_V` + (`OPEN_MAX_V` − `OPEN_MIN_V`) × loudness), loudness = level scaled 0..1 between `GATE_OPEN_DB` and `FULL_DB`, clamped | level < `GATE_CLOSE_DB` → Closing |
| Closing | +`CLOSE_V` for `CLOSE_S`, then → Closed | level ≥ `GATE_OPEN_DB` → Open at once (the next syllable interrupts the close) |

- **Hysteresis:** `GATE_CLOSE_DB` is below `GATE_OPEN_DB` so the mouth
  doesn't flutter at the threshold.
- **Opening slew limit:** the open voltage's magnitude may grow by at most
  `OPEN_SLEW_V_PER_S` per second, since stepping straight to −6 V strained
  the motor (Boss, 2026-09-28; see `OPEN_RAMP_S`). Falling magnitude and
  the close pulse are not limited; `Tb6612Motor` already zeroes the duty
  before a direction flip.
- **Stall guard:** if the open drive stays beyond `STALL_V` for more than
  `MAX_STALL_S` without a break, the open voltage is capped at
  `OPEN_MIN_V` until the level drops below `GATE_CLOSE_DB`.

Starting values. **These are guesses to tune by eye**, except where the
basis says calibration:

| Setting | Start | Basis |
|---|---|---|
| `OPEN_MIN_V` / `OPEN_MAX_V` | 2 V / 6 V | Calibration: relaxed open, fully open |
| `OPEN_SLEW_V_PER_S` | 24 V/s | The calibrated ramp: 6 V over `OPEN_RAMP_S` = 0.25 s |
| `CLOSE_V` / `CLOSE_S` | 0.5 V / 0.16 s | The random-speech demo's syllable close (0.5 V, 0.15 s), which Boss saw as smooth and lifelike (2026-09-28), rounded to whole 20 ms ticks; maybe a little slow |
| `STALL_V` / `MAX_STALL_S` | 5 V / 0.5 s | The mouth demo held −6 V for 0.5 s |
| `ATTACK_S` / `RELEASE_S` | 0.01 s / 0.08 s | Guess |
| `GATE_OPEN_DB` / `GATE_CLOSE_DB` / `FULL_DB` | −35 / −40 / −10 dBFS | Guess; depends on Boss's mic gain |
| `MOUTH_LEAD_MS` | 0 ms | Tune by eye |
| `MAX_BACKLOG_MS` | 200 ms | Guess |

All live together in `TalkSettings`. Every duration must be a whole
number of 20 ms ticks (as the 50 ms steps rule for the motor profiles).

### Tuning tool (`lipsync_wav.py`)

Plays a WAV file through the same talk loop without Mumble, on the Pi, with
a command-line override for every setting above, so Boss can tune by
watching the mouth. It refuses to run while `jack.service` is active (both
would drive the chip and the sound card), using the same check as
`calibrate.py`, moved into one shared helper.

### Mumble server and client

- `mumble-server` (Debian's package and unit) runs on the Pi, port 64738.
- Boss sets the server password in `/etc/mumble-server.ini`. The bot reads
  it from `JACK_MUMBLE_PASSWORD` in `/etc/jack/jack.env` (root:jack, 0640),
  loaded by `EnvironmentFile=`. Secrets never go in the repo, which is
  public.
- The bot joins the root channel as `Jack`. Boss connects the Mumble
  desktop client to `10.10.0.54:64738` and uses push-to-talk.

### Failure handling

| Failure | Behavior |
|---|---|
| Mumble server down, or bot disconnected | Bot reconnects (pymumble's `reconnect=True`, every 10 s, if the spike proves it works); the loop keeps playing silence, mouth closed, watchdog pinged. One log line per disconnect and per reconnect. |
| Gap in voice | Silence is played; the state machine closes the mouth. |
| Backlog | Dropped per `MAX_BACKLOG_MS`, logged. |
| ALSA underrun (xrun) | Recover the device, log, continue. |
| Other ALSA error, or I2C `OSError` | Propagates: both motors short-brake (`attempt_all`), the process exits, systemd restarts it (see Self-recovery). |
| Loop hangs | Watchdog kills and restarts after 10 s; the motor holds its last duty until then (accepted, as today). |
| SIGTERM | `SystemExit`: brake both motors, close ALSA, disconnect the bot. |
| `mumble-server` crashes | Its own unit restarts it; Jack sees a disconnect. |

## Self-recovery

The app must come back on its own from any failure, without a human:

- **Crash or any exit** (for example an I2C `OSError` from a loose HAT or a
  brownout, a bug, or an unexpected exit 0): systemd restarts it after 5 s
  (`Restart=always`, `RestartSec=5`). A restart re-initializes the
  PCA9685.
- **Never give up:** `StartLimitIntervalSec=0`, so repeated crashes (for
  example I2C not ready yet at boot) keep retrying forever.
- **Hang** (process alive but stuck, for example blocked on I2C):
  the systemd watchdog. The unit is `Type=notify`, `NotifyAccess=main`,
  `WatchdogSec=10`. The app sends `READY=1` once the motor is initialized
  and `WATCHDOG=1` about once a second from the talk loop. If no ping
  arrives within 10 s, systemd kills the app (SIGABRT) and restarts it.
  A hung process can't stop its own motor, so the motor holds its last
  duty until the restart (about 10 s + 5 s at worst).
- **Deliberate stop** (`systemctl stop jack`) is not overridden: systemd
  never restarts a unit it was told to stop.
- Not covered: automatic rollback of a bad commit. Recovery from a broken
  push is the next push.

## Code structure (hexagonal)

- `motor_test/ramp.py` (domain, pure): waveforms as signed 12-bit duty
  counts (negative = backward). `ramp_profile(peak_volts, supply_volts,
  steps)` is one cycle of a 0 → peak → 0 triangle that starts at 0, peaks
  at `steps // 2`, and omits the closing 0 so cycles chain seamlessly.
  `square_profile(high_volts, low_volts, supply_volts, steps)` holds
  `high_volts` for the first half and `low_volts` for the second.
  `constant_profile(volts, supply_volts, steps)` holds `volts` throughout.
  `segment_profile(segments, supply_volts, step_s)` plays each
  `(start_volts, end_volts, seconds)` segment in order: a hold when start
  equals end, otherwise a linear ramp that reaches `end_volts` on its last
  step. It rejects durations that are not a whole number of steps. All raise `ValueError` for a non-positive supply, a voltage outside the
  supply range, or an odd or too-small step count. Magnitudes cap at 4095
  (4096 would set the PCA9685 full-off bit).
- `motor_test/mouth.py` (domain, pure): the calibrated mouth poses as
  `(start_volts, end_volts, seconds)` segments built with `hold()` and
  `ramp()`: `CLOSE`, `RELAX`, `open_fully(seconds)` (ramps over
  `OPEN_RAMP_S` = 0.25 s, then holds) and `rest(seconds)`, plus
  `describe()` for log lines.
- `motor_test/ports.py`: the `MotorOutput` protocol, with
  `drive(count)` (signed) and `stop()`.
- `motor_test/pca9685.py` (adapter): `Pca9685(bus, address, pwm_freq_hz)`,
  the PWM chip, created once and shared by both motors. Register logic
  follows Waveshare's `PCA9685.py`, writing 12-bit counts directly
  (Waveshare's `setDutycycle` scales by 40 and never reaches the full
  4096). `set_off_count(channel, count)` rejects counts outside 0..4095.
- `motor_test/tb6612_motor.py` (adapter): `Tb6612Motor(chip, channels,
  reverse=False)`, a `MotorOutput` for one TB6612FNG channel.
  `MotorChannels(pwm, in1, in2)`, with `MOTOR_A = (0, 1, 2)` and
  `MOTOR_B = (5, 3, 4)`. `drive(count)` runs at duty `abs(count)`,
  forward for count ≥ 0 and backward below 0, and rewrites the direction
  pins only when the direction changes, zeroing the duty first so the old
  duty never briefly drives the new direction (seen on hardware as a
  +6 V blip when going from fully open to close). `stop()` short-brakes.
  `reverse=True` swaps the direction for a motor wired with flipped
  polarity. Signed drive, short brake and reverse are borrowed from
  https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT). That
  library drives TB6612 pins from Pi GPIO, which this HAT does not do, so
  only the ideas are borrowed.
- `motor_test/attempt_all.py`: `attempt_all(actions)` runs every action
  even if an earlier one raises, then re-raises the first failure. Used
  wherever braking must not be skipped.
- `motor_test/speech.py` (domain): `random_phrase(rng)` builds one
  talking phrase from the calibrated poses (see "Smoke test behavior");
  pass a seeded `random.Random` for repeatable output.
- `motor_test/smoke_test.py` (application): `run_generated_loop(motors,
  next_cycle, step_s, sleep, on_cycle)` asks `next_cycle()` for one count
  list per motor each cycle and plays them in lockstep: step i drives
  every motor with its i-th count, then waits `step_s`. It repeats forever,
  calls `on_cycle()` after each completed cycle, and always stops every
  motor when the loop exits (exception, unplayable cycle or SIGTERM), even
  if stopping one fails. `run_profiles_loop(profiles, ...)` is the
  fixed-profile form, checked before any motor is touched.
- `motor_test/systemd_notify.py` (adapter): `notify(message)` sends one
  sd_notify datagram to `$NOTIFY_SOCKET` using only the standard library.
  It does nothing when `NOTIFY_SOCKET` is unset (running by hand), and
  supports abstract-namespace sockets (`@` prefix).
- `motor_test/pcm.py` (domain, pure): the frame format (48 kHz mono
  16-bit, 20 ms = 960 samples), `silence()` and `mix(frames)`.
- `motor_test/envelope.py` (domain, pure): frame RMS in dBFS and the
  attack/release smoother.
- `motor_test/talk_settings.py` (domain, pure): `TalkSettings`, every
  tunable in the table above with its starting value, validated.
- `motor_test/lip_sync.py` (domain, pure): the mouth state machine; level
  in, signed volts out, one call per tick.
- `motor_test/frame_queue.py` (domain): `FrameQueue`, a thread-safe,
  bounded queue that cuts arbitrary PCM chunks into frames and drops the
  oldest past its limit.
- `motor_test/ports.py` gains `VoiceSource` (`take_frames()`: the next
  frame from each voice currently sounding) and `AudioSink` (`write(frame)`,
  blocking; `close()`).
- `motor_test/mumble_voice.py` (adapter): a `VoiceSource` with one
  `FrameQueue` per Mumble talker, fed by pymumble's sound callback, plus
  `connect_mumble()`. The only module that imports pymumble.
- `motor_test/wav_source.py` (adapter): a `VoiceSource` playing one WAV
  file, a frame per tick.
- `motor_test/alsa_sink.py` (adapter): an `AudioSink` on ALSA card 0 via
  `python3-alsaaudio`, riding through underruns. The only module that
  imports `alsaaudio`.
- `motor_test/service_guard.py`: `app_is_running()`, shared by
  `calibrate.py` and `lipsync_wav.py`.
- `motor_test/talk_loop.py` (application): `run_talk_loop`, taking the
  sources, an `AudioSink`, the mouth motor, the idle motors, the settings,
  the supply voltage, an `on_second` callback and an `until` check; always
  brakes every motor and closes the sink on exit.
- `lipsync_wav.py`: composition root for the tuning tool (a `WavSource`
  in place of Mumble).
- `main.py`: builds one `Pca9685`, both motors, the ALSA sink and the
  Mumble bot, installs a SIGTERM handler that raises `SystemExit` so the
  loop's cleanup runs, sends `READY=1` after init, and runs the talk loop
  with `on_second` sending `WATCHDOG=1`. `run_profiles_loop` and
  `MOUTH_DEMO` are no longer used by `main.py`; they stay in the repo
  (Boss, 2026-09-28).
- **Until the talk loop exists**, `main.py` plays random speech: it builds
  one `Pca9685` and motors A and B and runs `run_generated_loop` with
  `speaking_cycle(rng)` (motor A 0 V, motor B a fresh `random_phrase`),
  pinging the watchdog after each phrase. `demo_cycle()` gives the fixed
  `MOUTH_DEMO` instead.

## Testing

- pytest, run on the dev Mac (no hardware needed) for the domain and
  application layers:
  - Ramp starts at 0, peaks at 2048 for 6 V/12 V at index `steps // 2`,
    is symmetric, and has the requested number of steps. A full-supply
    peak caps at 4095. Square is +1024 for the first half and −1024 for
    the second for ±3 V/12 V, and caps at ±4095. Constant −6 V/12 V is
    −2048 at every step. The −6/−3/0 V holds at 50 ms steps are 10, 20
    and 40 steps, and a 0 → −6 V ramp over 0.25 s is −410, −819, −1229,
    −1638, −2048.
  - Invalid inputs raise `ValueError`.
  - `run_profiles_loop` drives each motor with its own profile in
    lockstep, repeats the cycle, stops every motor when interrupted
    (even if one stop fails), and rejects empty or mismatched profiles
    (the test's `sleep` raises after N steps to end the loop). This uses a
    recording fake `MotorOutput`, which checks our sequencing, not a
    mock's behavior.
  - `run_profiles_loop` calls `on_cycle` exactly once per completed cycle.
  - `run_generated_loop` plays fresh counts each cycle, pings after each,
    and brakes every motor on an unplayable cycle.
  - `notify` delivers the exact message to a real Unix datagram socket,
    does nothing without `NOTIFY_SOCKET`, and maps `@name` to an abstract
    address.
  - `jack.service` declares the self-recovery settings above.
- Self-recovery is verified on the Pi: `kill -9` (crash), `kill -STOP`
  (hang, so the watchdog fires), and a reboot, each followed by the app
  running again with no human action.
  - `Pca9685` writes the Waveshare init sequence and exact channel
    registers (recording in-memory bus), and rejects out-of-range counts.
  - `Tb6612Motor.drive` forward, backward, the reverse flag, duty on the
    right PWM channel for A and B, direction pins rewritten only on a
    direction change, and short brake (duty zeroed before IN1 = IN2 = high,
    still attempted if zeroing fails).
  - `main.demo_cycle` holds motor A at 0 V and plays the mouth demo
    (+341 ×5, 0 ×30, −683 ×10, 0 ×30, the 5-step ramp, −2048 ×10) over a
    4.5 s cycle, inside the 10 s watchdog with two cycles to spare.
  - `main.speaking_cycle` plays `random_phrase` on the mouth with motor A
    at 0 V, and consecutive cycles differ.
  - `random_phrase`, over 200 seeds: fits in 4.5 s, whole 50 ms steps,
    voltages within −6…+1 V, every opening ended by a close, ends closed
    then pauses, repeatable per seed, varied across seeds, and emphasis
    stays occasional.
  - The mouth poses match the calibration table.
  - Envelope: digital silence reads −90 dBFS, a full-scale sine reads
    about −3.01 dBFS, and a step input rises with `ATTACK_S` and falls
    with `RELEASE_S`.
  - Lip sync: gate and hysteresis, both ends of the proportional range,
    the opening slew limit, close pulse length, a syllable interrupting a
    close, and the stall guard engaging and releasing.
  - `FrameQueue` cuts chunks of any length into whole frames, keeps a
    partial frame for the next chunk, drops the oldest frames past its
    limit and reports how many, and stays consistent under concurrent puts.
    `MumbleVoice` keeps talkers apart and logs dropped backlog.
  - Talk loop, with recording fakes for `AudioSink` and `MotorOutput`
    (checking our sequencing, not a mock's behavior): silence when the
    sources are quiet, two sources mixed and clipped, mouth
    lead delays audio by whole ticks, `on_second` every 50 ticks, both
    motors braked on an exception.
  - `jack.service` runs the venv's Python and loads `/etc/jack/jack.env`;
    `jack-update.sh` re-runs pip only when `requirements-pi.txt` changed.
- The adapters are verified on real hardware: after install, watch
  `journalctl -u jack` and measure across MA1/MA2 and MB1/MB2 with a
  meter.
- Talking is verified on the Pi, with no mocks: the spike, then
  `lipsync_wav.py` while Boss tunes by eye, then end-to-end from Boss's
  Mumble client, then the self-recovery checks again (`kill -9`,
  `kill -STOP`, reboot, and `systemctl restart mumble-server`).

## Deployment

All units are system services in `/etc/systemd/system`. They start at boot
and are not tied to any login session or human user.

- **Checkout:** `/opt/jack`, owned by root, cloned from
  `https://github.com/robbiebyrd/jack.git` (public, no credentials).
- **`jack.service`:** runs `/opt/jack-venv/bin/python /opt/jack/main.py`
  as the dedicated system account `jack` (no home, nologin shell, in
  groups `i2c` and `audio`), with `EnvironmentFile=/etc/jack/jack.env`.
  Self-recovery settings as above; `WantedBy=multi-user.target`.
- **Python environment:** `/opt/jack-venv`, created with
  `--system-site-packages` so apt's `python3-smbus2`, `python3-alsaaudio`,
  `python3-opuslib` and `python3-protobuf` are used. Only pymumble comes
  from pip, pinned in `requirements-pi.txt` to the exact version or git
  commit the spike proved, and installed with `--no-deps` (pymumble pins
  protobuf 3.20.3; apt's 3.21.12 is used instead, if the spike shows it
  works).
- **`jack-update.service` + `jack-update.timer`:** runs as root every 60 s
  (`OnBootSec=60`, `OnUnitActiveSec=60`). Executes
  `/opt/jack/deploy/jack-update.sh`.
- **`deploy/jack-update.sh`:** `git fetch origin main`; if `HEAD` differs
  from `origin/main`, `git reset --hard origin/main` and
  `systemctl try-restart jack` (restarts only if running, so a deliberate
  stop is not overridden). If the new commit changed
  `requirements-pi.txt`, it runs the venv's `pip install -r` first, so
  code never deploys without its pinned dependency. Local edits on the Pi
  are discarded by design. The repo is the only source of truth.
- **`deploy/install.sh`** (run as root by Boss; safe to re-run): installs
  `git`, `mumble-server`, `python3-alsaaudio`, `python3-opuslib`,
  `python3-protobuf` and `python3-venv`, enables I2C
  (`raspi-config nonint do_i2c 0`), creates the `jack` system user (groups
  `i2c`, `audio`), clones to `/opt/jack`, creates the venv and installs
  `requirements-pi.txt`, creates `/etc/jack/jack.env` with a placeholder
  password only if it is missing (never overwrites it), installs and
  enables the units, and says to reboot so I2C takes effect.
- **Deploy signal:** after a deploy (only when a new commit landed),
  `jack-update.sh` runs `deploy/flash-leds.sh`, which blinks the Pi 4's
  onboard ACT (green) and PWR (red) LEDs with the kernel `timer` trigger
  (100 ms on, 100 ms off) for 10 s, then restores each LED's previous
  trigger (normally `mmc0` and `default-on`), also when cut short. A
  flash failure is logged but does not fail the deploy. `JACK_LEDS_DIR`
  (default `/sys/class/leds`) lets tests use a fake LED directory.
- **Logs:** `journalctl -u jack -u jack-update`.
- **Accepted risk:** `jack-update.service` runs
  `/opt/jack/deploy/jack-update.sh` as root, and that script is replaced
  from the repo on every deploy, so anyone who can push to `main` can run
  code as root on the Pi. Boss accepted this deliberately in exchange for
  the updater updating itself.

## Out of scope

- Return audio (a microphone at Jack so Boss hears visitors). Its own spec
  later. A class-compliant USB mic is preferred. Bluetooth was considered
  and deferred: headset-profile mics are 8–16 kHz narrowband and drag
  Bluetooth speaker output down with them, they need a Bluetooth audio stack
  the Pi doesn't have, and the Pi 4's Bluetooth shares a radio with its
  Wi-Fi, which carries the voice link.
- A physical line-in (USB sound card) for audio from devices Jack doesn't
  control. The audio source sits behind `VoiceSource`, so it would be a new
  adapter.
- Icecast streaming (and MuSE, a source client whose last commit was
  2010): too much buffering for a two-way conversation.
- Access from outside the LAN (a VPN such as Tailscale would be the path).
- Playing pre-recorded clips from the app, and what triggers them. The
  talk loop's source mixing is built so a clip source plugs in later.
- Motor A behavior beyond holding 0 V.
- The TB6612FNG `STBY` pin. Waveshare's sample code never drives it.
- Push-based deploys (webhooks, self-hosted runners).
