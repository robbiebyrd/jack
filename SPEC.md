# Jack: Raspberry Pi Motor HAT App with Git-Driven Deploys

## Purpose

The Raspberry Pi `jack` (10.10.0.54) runs `main.py` from this repository
as a boot-time system service. When `main` on GitHub changes, the Pi pulls
the new commit and restarts the app automatically. The initial `main.py` is
a hardware smoke test for the Waveshare Motor Driver HAT that proves the
whole chain works: push → Pi pulls → app restarts → motor output moves.

Next, Jack talks: Boss speaks into a Mumble client on a computer on the
same LAN, Jack plays the voice from its speaker, and the animatronic's
mouth (motor A) moves in sync with it (see "Talking").

Next, show control: a second HAT adds three motors (hand, arm pivot,
elbow), and an HTTP and an OSC server let an external show controller
command all four motors, with the mouth switchable between following the
live voice and following commands (see "Show control").

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
- **Second HAT (added 2026-10-02):** same board, stacked, VIN bridged from
  the first HAT's 12 V. Boss soldered the pad labelled "A4", but a
  read-only I2C scan shows it answering at **`0x41`** (power-on MODE1
  `0x11`), i.e. the pad acts as A0 (A4 would give `0x50`; Waveshare:
  address = `0x40` + bridged pads, A0 = 1 … A4 = 16). `0x41` is used. Every
  PCA9685 also answers the All Call address `0x70`; the app never uses it.
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
motor A) and how. The talk loop (see "Talking") replaces this demo in
`main.py`; the calibration table below stays the basis for its settings. The
numbered behaviour below is that retired demo, kept for `speaking_cycle` and
`demo_cycle`, which `main.py` still defines.

Mouth calibration, measured by Boss with `calibrate.py` (2026-09-28):

| Pose | How to reach it | Held without power? |
|---|---|---|
| Closed | +1 V for 0.25 s closes it from any pose; +0.5 V is enough from relaxed open | yes |
| Relaxed open | apply −2 V, then release: the mouth settles here | yes |
| Fully open | −6 V | no: only while −6 V is held |

Holding a voltage keeps the motor running against resistance (stalled), so
prefer the closed and relaxed-open poses and keep fully open short.

Since the mouth moved to channel A (2026-10-02) its signs are reversed:
positive opens and negative closes (see "Poses and per-motor settings").
The retired demo's segments (`jack/show/motion/mouth.py`, `jack/show/motion/speech.py`)
still use the old signs.

1. Drive both motor channels in lockstep in 50 ms steps. Each motor has
   its own signed profile; positive means forward (IN1 = 0, IN2 = 1, so
   terminal 1 is positive), negative means backward (IN1 = 1, IN2 = 0).
2. **Motor A (the mouth)** imitates speaking until control inputs (audio,
   DMX, websockets) are wired in. Each cycle plays a fresh random phrase from
   `jack/show/motion/speech.py`, at most 4.5 s long:
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
3. **Motor B** held 0 V for the whole cycle. Now motor B is the hand: it
   follows show control and rests when uncommanded (the demo functions
   still keep it at 0 V).
4. Repeat the cycle back-to-back, with no pause, until the process is
   stopped.
5. On SIGTERM or any exception, **short-brake** every motor before exiting:
   duty 0, then IN1 = IN2 = high (the TB6612FNG shorts the motor leads).
   A failure braking one motor must not prevent braking the other.

## Calibration tool (`calibrate.py`)

For finding a motor's voltages and positions interactively on the Pi,
without a commit per try (see also "Calibration for every motor"):

- Stop the app first (`sudo systemctl stop jack`); the tool refuses to run
  while `jack.service` is active, since both would drive the same chips.
- Run `sudo -u jack /opt/jack-venv/bin/python /opt/jack/calibrate.py <motor>`
  (`mouth`, `hand`, `pivot` or `elbow`). It runs as `jack` because only
  root and the `jack` group can read `/etc/jack` (it holds the Mumble
  password), and `jack` is in the `i2c` group; if `/etc/jack` is
  unreadable the tool says to run it as `jack`. It
  names the motor's HAT and channel, and warns if its poses are
  uncalibrated placeholders. Each line `<volts> <seconds>` drives that motor
  (negative = backward), then short-brakes. A pose name from `poses.toml`
  (`close`, `relax`, `open` for the mouth; `curl`, `left`, `right`, `up` for
  the others), optionally followed by seconds, ramps from 0 V to the pose's
  volts at the motor's `slew_v_per_s` (rounded up to whole steps), then holds
  for the pose's seconds or the ones given. Commands play in 50 ms steps, so
  durations must be whole steps (0.8 works, 0.33 is rejected). `q` or end of
  input quits.
- Volts must be within ±supply; seconds must be more than 0 and at most
  3 s, to limit stall heating against an end stop. Rejected lines never
  move the motor.
- The motor brakes after every move and on every exit: `q`, end of input,
  Ctrl-C, and I2C errors.
- Code: `jack/application/calibration.py` (`parse_command`, `run_calibration`,
  talking to motors only through `MotorOutput`) and `calibrate.py`
  (composition root reusing `main.py`'s hardware constants and
  `POSES_PATHS`).

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
updates the envelope, computes the mouth voltage and drives motor A.
With show control (see "Show control") the same tick also drives the
hand (motor B), pivot and elbow from the command board, and in `show` mode
the mouth too; the hand follows show control and rests when uncommanded.

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
motor A out.

| State | Motor A | Next |
|---|---|---|
| Closed | 0 V (short brake; the mouth holds closed unpowered) | level ≥ `GATE_OPEN_DB` → Open |
| Open | −(`OPEN_MIN_V` + (`OPEN_MAX_V` − `OPEN_MIN_V`) × loudness), loudness = (level scaled 0..1 between `GATE_OPEN_DB` and `FULL_DB`, clamped) ^ `OPEN_CURVE` | level < `GATE_CLOSE_DB` → Closing |
| Closing | +`CLOSE_V` for `CLOSE_S`, then → Closed | level ≥ `GATE_OPEN_DB` → Open at once (the next syllable interrupts the close) |

- **Hysteresis:** `GATE_CLOSE_DB` is below `GATE_OPEN_DB` so the mouth
  doesn't flutter at the threshold.
- **Opening slew limit:** the open voltage's magnitude may grow by at most
  `OPEN_SLEW_V_PER_S` per second, since stepping straight to −6 V strained
  the motor (Boss, 2026-09-28; see `OPEN_RAMP_S`). Falling magnitude and
  the close pulse are not limited; `Tb6612Motor` already zeroes the duty
  before a direction flip.
- **Stall budget:** each open tick spends the mouth's stall budget, the
  same one show control uses (see "Command board"): `TICK_S / hold(volts)`
  from the mouth's `holds` in `poses.toml`. When an opening spends it, the
  mouth plays its close pulse and stays closed, even through loud speech,
  until the level drops below `GATE_CLOSE_DB` (a pause). Every close
  refills it, so normal speech with gaps never reaches it; a long loud
  phrase does (about 0.5 s fully open, 1–2 s part open). It replaced a
  stall guard (cap at relaxed open after 0.5 s beyond 5 V) that allowed
  unlimited drive at or below 5 V (Boss, 2026-10-03).

Starting values. **These are guesses to tune by eye**, except where the
basis says calibration. The rows marked "now poses.toml" no longer live in
`TalkSettings`; see the note under the table:

| Setting | Start | Basis |
|---|---|---|
| `OPEN_MIN_V` / `OPEN_MAX_V` (now poses.toml `[mouth]` `min_v` / `max_v`) | 1 V / 6 V | `OPEN_MAX_V`: calibration, fully open. `OPEN_MIN_V`: Boss, live tuning 2026-09-28: soft sounds too broad, hard sounds too little (was 2 V, the calibrated relaxed open). Risk: the calibration never tested below 2 V; it is unknown that 1 V moves the mouth |
| `OPEN_SLEW_V_PER_S` (now poses.toml `[mouth]` `slew_v_per_s`) | 48 V/s | Boss: respond twice as fast (2026-09-28). Risk: the calibrated ramp was 24 V/s (6 V over `OPEN_RAMP_S` = 0.25 s) because stepping to -6 V strained the motor; 48 V/s trades some of that margin for speed |
| `OPEN_CURVE` | 2.0 | Boss: too sensitive, wanted a curve (2026-09-28); medium syllables open ~a quarter |
| `CLOSE_V` / `CLOSE_S` (now poses.toml `[mouth]` `rest_pulse_v` / `rest_pulse_s`) | 0.5 V / 0.08 s | Boss: respond twice as fast (2026-09-28) (whole 20 ms ticks); the random-speech demo Boss saw as lifelike used 0.5 V for 0.15 s |
| `ATTACK_S` / `RELEASE_S` | 0.01 s / 0.04 s | `ATTACK_S` guess; `RELEASE_S`: Boss: respond twice as fast (2026-09-28) |
| `GATE_OPEN_DB` / `GATE_CLOSE_DB` / `FULL_DB` | −22 / −27 / −14 dBFS | Boss, live tuning 2026-09-28: soft sounds too broad, hard sounds too little. Measured from Boss's voice via Mumble on the Pi (per 20 ms frame: p75 −24, p90 −15.7, p95 −13.1, p99 −9.6 dBFS; above −25 dBFS 27% of frames, above −20 18%): the open gate at −22 opens the mouth on ~22% of frames, and `FULL_DB` −14 lets hard syllables (p90 to p95) reach fully open |
| `MOUTH_LEAD_MS` | 0 ms | Tune by eye |
| `MAX_BACKLOG_MS` | 200 ms | Guess |

All live together in `TalkSettings`. Every duration must be a whole
number of 20 ms ticks (as the 50 ms steps rule for the motor profiles).

**With show control (Boss, 2026-10-02, "one place per motor"):** the
mouth's physical facts — `OPEN_MIN_V`, `OPEN_MAX_V`, `OPEN_SLEW_V_PER_S`,
`CLOSE_V`, `CLOSE_S` — move out of `TalkSettings` into the mouth's entry in
`poses.toml` (its range, slew and rest pulse; values unchanged). Lip sync
reads them from there, exactly as show mode does, so one retune covers both
modes. `TalkSettings` keeps only lip-sync behaviour (gates, `FULL_DB`,
`OPEN_CURVE`, attack/release, mouth lead, backlog). Their
`jack.env` overrides go away; if any of `JACK_OPEN_MIN_V`,
`JACK_OPEN_MAX_V`, `JACK_OPEN_SLEW_V_PER_S`, `JACK_CLOSE_V` or
`JACK_CLOSE_S` is still set, the app refuses to start with a one-line
message saying to move it to `/etc/jack/poses.toml`.

Every setting can be overridden on the Pi in `/etc/jack/jack.env` as
`JACK_<SETTING_NAME>` (e.g. `JACK_GATE_OPEN_DB=-20`), then
`sudo systemctl restart jack`. An invalid value (not a number, an impossible
combination, or more volts than the supply) stops the app with a one-line
message in `journalctl -u jack`.

### Tuning tool (`lipsync_wav.py`)

Plays a WAV file through the same talk loop without Mumble, on the Pi, with
a command-line override for every setting above, so Boss can tune by
watching the mouth. It refuses to run while `jack.service` is active (both
would drive the chip and the sound card), using the same check as
`calibrate.py`, moved into one shared helper. Like `calibrate.py`, it runs
as `jack` (`sudo -u jack`) so it can read `/etc/jack/poses.toml`.

### Mumble server and client

- `mumble-server` (Debian's package and unit) runs on the Pi, port 64738.
- Boss sets the server password in `/etc/mumble/mumble-server.ini`. The bot reads
  it from `JACK_MUMBLE_PASSWORD` in `/etc/jack/jack.env` (root:jack, 0640),
  loaded by `EnvironmentFile=`. Secrets never go in the repo, which is
  public.
- The bot joins the root channel as `Jack`. Boss connects the Mumble
  desktop client to `10.10.0.54:64738` and uses push-to-talk.
- Mumble stays as a fallback to ROC (see "ROC voice"); both run at once.

### ROC voice (designed with Boss, 2026-10-03)

Boss replaces Mumble as the everyday way to talk through Jack, for less
complexity and less delay: any Mac app's sound goes to a Roc Toolkit
virtual output device (roc-vad) and streams over the LAN to the Pi. Mumble
stays as a fallback; Jack listens to both and mixes them.

Measured in a throwaway spike (2026-10-03): roc-vad 0.0.4 on Boss's Mac
(macOS 27.2) streams to `roc-recv` 0.4.0 (Debian 13 package
`roc-toolkit-tools`) on the Pi. The sender sends 44.1 kHz; `roc-recv`
resamples. ROC's own end-to-end latency over the Wi-Fi LAN: target 200 ms
(default) → ~190 ms, 120 ms → ~112 ms, 90 ms → ~80 ms, all one steady
session; 60 ms broke up (16 session restarts in 30 s, ~2.6 s of gaps).
`roc-recv -o file:- --output-format s16 --rate 48000` writes raw stereo
signed 16-bit samples to stdout in real time, both channels identical, and
silence while nobody sends.

- **Adapter:** `jack/adapters/audio/roc_voice.py`, a `VoiceSource`. It
  starts `roc-recv` as a child process:
  `roc-recv -s rtp+rs8m://0.0.0.0:<source> -r rs8m://0.0.0.0:<repair>
  -c rtcp://0.0.0.0:<control> -o file:- --output-format s16 --rate 48000
  --target-latency=<ms>ms`. A reader thread reads its stdout, keeps the
  left channel (mono, signed 16-bit, 48 kHz: the talk loop's format), and
  puts it in a `FrameQueue` (`MAX_BACKLOG_MS`), which cuts 20 ms frames and
  drops the oldest audio if Jack falls behind. `take_frames()` returns the
  next frame, or none. The command to start is injected, so tests run a
  fake `roc-recv`.
- **Mixing:** the talk loop gets `[mumble_voice, roc_voice]` and mixes
  whatever each has, as it already does for several Mumble talkers.
- **Settings** (`/etc/jack/jack.env`, checked at startup; a bad value stops
  the app with a one-line message naming it): `JACK_ROC_SOURCE_PORT`
  (default 10001), `JACK_ROC_REPAIR_PORT` (10002), `JACK_ROC_CONTROL_PORT`
  (10003), ports 1–65535; `JACK_ROC_TARGET_LATENCY_MS` (default 100, the
  lowest steady spike value plus margin), a positive number. The right
  latency depends on the network, so it is tuned on site.
- **Install:** `deploy/install.sh` adds `roc-toolkit-tools` to its
  `apt-get install` list.
- **Mac side** (README "Talking over ROC"): install roc-vad, then
  `roc-vad device add sender --name "Jack"` and
  `roc-vad device connect <id> --source rtp+rs8m://10.10.0.54:10001
  --repair rs8m://10.10.0.54:10002 --control rtcp://10.10.0.54:10003`, and
  choose "Jack" as the sound output.
- **Out of scope:** an OSC/HTTP "ROC receiving" indicator, choosing one
  source over the other, and sending audio back to the Mac.

### Failure handling

| Failure | Behavior |
|---|---|
| Mumble server down, or bot disconnected | Bot reconnects (pymumble's `reconnect=True`, every 10 s, if the spike proves it works); the loop keeps playing (ROC, or silence), watchdog pinged. One log line per disconnect and per reconnect. |
| Gap in voice | Silence is played; the state machine closes the mouth. |
| Backlog | Dropped per `MAX_BACKLOG_MS`, logged. |
| ALSA underrun (xrun) | Recover the device, log, continue. |
| Other ALSA error, or I2C `OSError` | Propagates: all four motors short-brake (`attempt_all`), the process exits, systemd restarts it (see Self-recovery). |
| Loop hangs | Watchdog kills and restarts after 10 s; the motor holds its last duty until then (accepted, as today). |
| SIGTERM | `SystemExit`: brake all four motors, close ALSA; the bot's daemon thread ends with the process, closing its connection. |
| pymumble's thread dies (e.g. the server rejects the password) | Logged once ("Mumble stopped; ROC still works"); Jack keeps running on ROC. `/jack/mumble` feedback and `/status` show Mumble disconnected; restarting Jack retries it. (Before ROC, this exited so systemd restarted Jack.) |
| `roc-recv` missing at startup | Exits with "roc-recv not found: sudo apt install roc-toolkit-tools"; systemd keeps retrying. A broken install fails loudly, as a missing HAT does. |
| `roc-recv` exits while running | Its reader sees end of output, logs "roc-recv exited (code N); restarting" (at most one line a minute), waits 2 s and starts it again. Motors, Mumble and the loop carry on. |
| Shutdown (SIGTERM or any exit) | `roc-recv` is terminated (SIGTERM, then killed after 1 s); systemd's stop also ends anything left in the service's process group. |
| `mumble-server` crashes | Its own unit restarts it; Jack sees a disconnect. |

## Show control

Designed with Boss, 2026-10-02. An external show controller (QLab, a
lighting desk, TouchDesigner, a script…) commands Jack's four motors over
OSC or HTTP. Voice audio keeps playing in every mode; only the mouth's
source of movement switches.

### Motors

| Name | Board | Channel | Rest | Continuous range |
|---|---|---|---|---|
| `mouth` | `0x40` | A | closed | 0.0 … 1.0 (open) |
| `hand` | `0x40` | B | stays where it stopped | −1.0 (open) … 0.0 … +1.0 (curled inward) |
| `pivot` | `0x41` | A | stays where it stopped | −1.0 (left) … 0.0 … +1.0 (right) |
| `elbow` | `0x41` | B | 90° down | 0.0 … 1.0 (up, towards 30°) |

- `jack/show/motion/motors.py` (pure) is the registry: name, board address,
  channel, and whether the range is one-sided or two-sided (hand, pivot).
- Boss wires the new motors to these channels. On the first HAT, Boss
  confirmed (2026-10-02) the mouth is on channel A and the hand on B. The
  hand and pivot don't spring back: driving moves them and they stay put, so
  they are two-sided and rest is a brake in place. The elbow doesn't respond
  yet and is uncalibrated.
- Both boards are required: if either fails at startup the app exits with a
  clear message and systemd keeps retrying (no silent degraded mode).
- Every motor is braked at startup, each HAT's motors as soon as that HAT
  is up (before the next HAT, ALSA or anything else that might fail, since a
  crash without cleanup can leave a HAT at its last duty), and every exit
  brakes all four (`attempt_all`).

### Poses and per-motor settings (`poses.toml`)

`poses.toml` in the repo holds the defaults; `/etc/jack/poses.toml` on the
Pi, if present, overrides any entry. Read at startup (`tomllib`, standard
library); an invalid file stops the app with a one-line message naming the
file and entry. Per motor:

- `min_v`, `max_v`, `sign`: the magnitude in volts that 1.0 maps to
  (`max_v`), the volts at the low end just past rest (`min_v`), and the sign
  of "positive" (for the pivot: the sign of "right"; left is the opposite).
  `calibrated` marks whether the entry was measured.
- `slew_v_per_s`: how fast the drive may grow (all motors, as the mouth).
- `holds`: the motor's measured safe drive times, a list of
  `{ volts = <V>, seconds = <s> }` points (stall protection; "hold" is the
  safe total time driven at that voltage). The hold at a voltage V is read
  only from points with V's sign: interpolated linearly on |V| between
  points, and the lowest point's hold below it (lower volts hold longer).
  `max_hold_s`, the single cutoff it replaces, is refused with a message
  saying to write `holds` instead.
- `rest`: `"brake"` (short brake, the default and today's behaviour) or
  `"coast"` (leads open), in case braking slows a spring return; plus an
  optional rest pulse (`rest_pulse_v`, `rest_pulse_s`) played on the way to
  rest (the mouth's close pulse −0.5 V, 0.08 s, since it holds its pose
  unpowered).
- `poses`: named poses, each a signed voltage and a default duration.
  Poses have no ramp of their own: every move ramps at the motor's
  `slew_v_per_s`, in show control and in `calibrate.py` alike.
- A non-zero `rest_pulse_s` must be whole 20 ms ticks. Unknown keys
  (typos) are rejected.
- Nothing may run on an unmeasured hold. The file is refused at load if a
  hold point's seconds aren't positive or its volts are 0, or if any of
  these has no hold point at or above its magnitude in its direction: the
  continuous range (`sign × max_v`, and `−sign × max_v` for a two-sided
  motor), any pose, the rest pulse. It is also refused if a pose lasts
  longer than the hold at its volts, or the rest pulse longer than the hold
  at its volts.

The mouth's entry is the single source of its voltages for both lip sync
and show mode (see the note under "Mouth control").

Values: Boss's calibration of 2026-10-02 with `calibrate.py` ("hold" is
the safe total time driven at a voltage):

| Motor | Direction | Moves at | Safe hold |
|---|---|---|---|
| mouth | + opens, − closes | open 2–6 V (1 V opens very slightly), close −0.25 to −5 V | 2 V 2 s, 6 V 0.5 s, −0.25 V 1 s, −5 V 0.25 s |
| hand | + curls, − opens | curl 3–6 V, open −0.5 to −6 V | 6–5 V 2 s, 3 V 5 s, −5 V 0.5 s, −3 V 1 s, −1 V 4 s |
| pivot | + right, − left | 6–7 V | 6–7 V 4 s |

A full curl from open is +6 V for 1.5 s; a full open from curled is −5 V for
0.75 s; 4 s at ±6 V swings the pivot fully across.

In `poses.toml`: the mouth keeps its lip-sync range 1–6 V (1 V for
fidelity), slew 48 V/s, close pulse −0.5 V/0.08 s, poses `close` −1 V,
`relax` +2 V, `open` +6 V. The hand runs 3–6 V both ways (the lowest volts
that move it both ways), poses `curl` +6 V and `open` −5 V. The pivot runs
6–7 V, poses `left`/`right` ±6 V for 2 s (about centre to one side). The
hand and pivot slew at 24 V/s (not measured). `holds` are the measured
points above (hand: +6 V and +5 V 2 s, +3 V 5 s, −1 V 4 s, −3 V 1 s, −5 V
0.5 s; pivot ±6 V and ±7 V 4 s). The hand's `max_v` is 5 V, since its
opening holds were measured only to −5 V; its `curl` pose is the full
+6 V for 1.5 s (within the 2 s hold) and `open` is −5 V for 0.5 s (a full
open, 0.75 s, is over the −5 V hold, so it takes two). The holds govern
show control, poses and lip sync alike.
The elbow (`up`) stays a **placeholder, marked uncalibrated in the file**:
2 V, 0.5 s pose, holds ±2 V 1 s, 24 V/s slew, positive sign. Volts beyond the 12 V
supply are rejected.

### Command board (`jack/show/control/control_board.py`)

Thread-safe; the OSC and HTTP threads write, the talk loop reads once per
20 ms tick. The clock is injected (tests need no sleeping). Per motor it
holds the latest command — a **value** (with arrival time) or a **pose**
(name, start time, duration) — and answers "what voltage now?":

- A value holds while new values keep arriving; after
  `control_timeout_s` (default 5 s, `JACK_CONTROL_TIMEOUT_S`; longer than the
  longest hold, since TouchOSC doesn't resend a fader held still) with none,
  the motor goes to rest (dead-man: covers a crashed controller or a
  dropped network).
- A pose plays for its default duration or the one sent with the command,
  then the motor goes to rest.
- A new command for a motor replaces the previous one at once; a rest
  command sends it to rest at once.
- Max hold: a stall budget. Each driven tick at the volts actually applied
  (after slew) uses `TICK_S / hold(volts)` of it; when the budget is spent
  the motor is forced to rest until a rest command arrives or commands
  stop. Any rest refills the budget at once. Rest-pulse ticks use none (the
  pulse is checked against the holds at load). `jack/show/motion/stall_budget.py`
  holds the budget; lip sync spends the mouth's the same way (see "Mouth
  control"). So 0.25 s at 6 V and 1 s at
  2 V on the mouth (half of 0.5 s, half of 2 s) together spend it.
- The slew limit applies only to driving harder (a growing magnitude, or a
  reversal of direction). Easing off is immediate, and so are going to rest
  and the jump to the rest pulse: going to rest plays the motor's rest
  pulse, then brake or coast.

The network threads never touch a motor: the talk loop is the only owner of
the hardware, so the existing watchdog and shutdown path cover everything.
Per-motor tick logic (slew, max hold, rest pulse, brake/coast) lives in
`jack/show/motion/motor_driver.py` (pure). The loop writes a motor only when its
drive changes, so a steady value or a resting motor costs no I2C traffic
(a duty change is 4 single-byte I2C writes and a brake 12, so rewriting
four motors every 20 ms tick would cost 16–48 writes per tick).
Coasting needs `coast()` on `MotorOutput` (`Tb6612Motor`: duty 0, IN1 = IN2
= low, which the TB6612FNG treats as stop with the leads open — to confirm
on the hardware).

### Mouth mode

- `live` (default): the mouth follows the voice (lip sync, as today);
  mouth commands are refused: HTTP answers `409`, OSC ignores and logs them
  (rate-limited).
- `show`: the mouth follows the command board like the other motors; the
  voice still plays.
- Switched at runtime by OSC `/jack/mouth/mode live|show` or
  `POST /mouth/mode`. The startup mode comes from `JACK_MOUTH_MODE` in
  `jack.env` (`live` or `show`); a restart returns to it (not persisted).
- A switch never jumps the mouth: to `show`, the mouth's driver takes over
  from the volts lip sync left it at, so it slews from there or, with no
  command, plays the rest pulse before braking; to `live`, lip sync starts
  fresh (closed) and opens with its own slew. A real switch drops any mouth
  command, so a stale show command can't resume after a later switch back.

### OSC (UDP, default port 9000, `JACK_OSC_PORT`)

| Address | Arguments | Effect |
|---|---|---|
| `/jack/<motor>` | float | Continuous value (0…1; hand and pivot −1…1) |
| `/jack/<motor>/pose` | string, optional float | Named pose, optional duration (s) |
| `/jack/<motor>/rest` | — | That motor to rest |
| `/jack/rest` | — | All motors to rest |
| `/jack/mouth/mode` | `live` or `show` | Switch the mouth's source |

Out-of-range values are clamped (logged); unknown motors, poses or
addresses, and mouth commands in `live` mode, are ignored and logged; all
these log lines are rate-limited to one per kind per minute
(`jack/support/rate_limited_log.py`). No
authentication: anyone on the LAN may send commands (Boss, 2026-10-02).
Library: `python-osc` (pip, pinned in `requirements-pi.txt` and
`requirements-dev.txt`; no dependencies of its own, Python ≥ 3.10; not
packaged by Debian), imported only in `jack/adapters/network/osc_server.py`. Bundle
timetags are ignored (`Dispatcher(strict_timing=False)`): every message
acts on arrival, so a future timetag can't hold a thread asleep; request
threads are daemon threads so they never keep the process from exiting.

### OSC replies and feedback (designed with Boss, 2026-10-02)

Jack answers and reports state over OSC, as well as taking commands.

| You send (UDP 9000) | Jack does |
|---|---|
| `/jack/ping` | Replies `/jack/pong` (no arguments) |
| `/jack/status` | Replies once with the full state (below) |
| `/jack/subscribe` | Optional int port. Pushes feedback to the sender's IP at that port, or at the reply port. A subscription lasts 60 s; resend to keep it. |
| `/jack/unsubscribe` | Optional int port. Stops pushing to the sender's IP at that port, or at the reply port. |

- **Reply port:** the sender's IP, at `JACK_OSC_REPLY_PORT` from `jack.env` if set (1–65535), otherwise the sender's source port. This is needed because some apps send from one port and listen on another (Boss's app sent from 9000 and listened on 21601).
- **State messages,** for the status reply and for feedback:
  - `/jack/<motor>/volts` (float: the volts the talk loop last drove)
  - `/jack/<motor>/max_hold` (int: 1 while max hold has tripped)
  - `/jack/mouth/mode` (string: `live` or `show`)
  - `/jack/mumble` (int: 1 when connected)
- **Feedback timing:** every 20 ms, each subscriber gets the messages whose value changed since the last check. Each also gets the full set once a second, and at once when it subscribes or renews, so lost UDP packets heal and a new subscriber is up to date immediately.
- **Limits:**
  - At most 8 subscribers. A 9th subscription is refused and logged (rate-limited); existing subscribers keep theirs.
  - A subscriber that hasn't renewed within 60 s is dropped.
  - A send error to one subscriber is logged (rate-limited) and doesn't stop the others.
- **Structure:**
  - `jack/show/control/osc_feedback.py` (pure, injected clock) turns a ControlBoard status into state messages, detects changes per subscriber, and keeps the subscriber list (expiry, cap).
  - A daemon "osc-feedback" thread in `jack/adapters/network/osc_server.py` reads the board every 20 ms and sends. The talk loop never does network I/O, so a slow network can't stall the motors.
  - The ping, status, subscribe and unsubscribe routes are parsed in `show_commands.py` like every other command (validation, 400/404). Replies leave through the OSC server's own socket, from port 9000.
- **Not provided:** OSC over TCP, authentication, protection against a LAN host subscribing on another host's behalf or filling the 8 slots (follows from no authentication; bounded by the cap and the 60 s lease), and feedback over HTTP (HTTP has `/status`).

### TouchOSC support (Boss uses TouchOSC; designed 2026-10-02)

TouchOSC buttons send a number (typically 1 on press, 0 on release). Faders send 0–1 floats, and controls update themselves when a message arrives on their own address. Jack accommodates this without scripting in the layout. This is based on general knowledge of TouchOSC, to be confirmed with Boss's layout.

1. **Float ports:** `/jack/subscribe` and `/jack/unsubscribe` accept a whole-number float port (`21601.0` means 21601). A fractional value, a bool, NaN or infinity, or one outside **1024**–65535 is a 400 ("port must be a whole number 1024-65535"). The floor stops a button left at its default press value of 1 from subscribing port 1. `JACK_OSC_REPLY_PORT` parsing is unchanged.
   - **Subscribe buttons:** `/jack/subscribe` and `/jack/unsubscribe` follow the button rule for a release: a single argument equal to 0 is ignored silently (no log, no reply).
   - **Commands renew subscriptions:** any OSC message Jack accepts (a command, an ignored release or a query; not a rejected one) extends the lease of every live subscription to the sender's IP by 60 s, without a full refresh. A layout in use therefore stays subscribed; an explicit `/jack/subscribe` still sends the full set.
2. **Buttons on argument-less commands:** `/jack/rest`, `/jack/<motor>/rest`, `/jack/ping` and `/jack/status` also accept one numeric argument. A non-zero value (press) acts, 0 (release) is ignored silently, and no argument acts as today. A string or a second argument is still a 400, and so is NaN or infinity ("button value must be a finite number"). The same applies to the numeric mouth mode in item 5.
3. **One address per pose:** `/jack/<motor>/pose/<name>` plays that pose for its default duration, for example `/jack/elbow/pose/up` or `/jack/pivot/pose/left`. It follows the same button rule as item 2. An unknown pose is a 404. `/jack/<motor>/pose <name> [seconds]` is unchanged. The per-pose address is OSC-only; HTTP keeps `POST /<motor>/pose`.
4. **Fader feedback:** subscribers and the status reply also get `/jack/<motor>` (float). It carries the motor's current value command (0–1, or −1–1 for the pivot), or 0.0 when the motor rests or is playing a pose. A fader on `/jack/<motor>` therefore follows what Jack is doing, for example dropping to 0 when the dead-man rule rests the motor.
5. **Mouth mode toggle:**
   - `/jack/mouth/mode` also accepts a number: non-zero means `show`, 0 means `live`.
   - `/jack/mouth/mode/show` takes the same number as input.
   - Feedback includes `/jack/mouth/mode/show` (float 1.0 in show, 0.0 in live), so one toggle on that address both switches the mode and lights up correctly. Use a **toggle** button there: a momentary button's release (0) would switch back to live.
   - The string forms (`live`/`show`) are unchanged.

Measured 2026-10-03 (Jack's own feedback while Boss held the hand fader): a TouchOSC fader held still does not resend, so the old 0.5 s dead-man rested the hand under Boss's finger. So the generated layout's faders snap back on release (value `x` default 0, or 0.5 for two-sided faders, `defaultPull` 100), sending rest the moment they are let go, and the dead-man default is 5 s, longer than the longest hold (the pivot's 4 s); each motor's `holds` still cap any drive. Still unverified: whether TouchOSC re-sends values it receives.

Feedback order: each motor's `/jack/<motor>`, `volts` and `max_hold`, then `/jack/mouth/mode` (string), `/jack/mouth/mode/show` (float), then `/jack/mumble`. Feedback values meant for TouchOSC controls are floats.

### HTTP (TCP, default port 8080, `JACK_HTTP_PORT`)

Python's `ThreadingHTTPServer`, no new dependency. JSON in and out:

- `POST /<motor>` `{"value": 0.6}`; `POST /<motor>/pose`
  `{"name": "curl", "seconds": 2}` (seconds optional);
  `POST /<motor>/rest`; `POST /rest`; `POST /mouth/mode` `{"mode": "show"}`.
- Bad input (including a bad `Content-Length`) → `400`, unknown motor,
  pose or path → `404`, a mouth command while the mouth is in `live` mode →
  `409`, a body over 4 KiB → `413`, an unexpected error → `500` (also logged
  to stderr), each with a one-line JSON error. Request logging is off (a held slider sends ~20
  requests a second).
- Each connection times out after 5 s of silence (`REQUEST_TIMEOUT_S`), so a
  phone dropping Wi-Fi mid-request can't hold a thread forever; a body that
  doesn't arrive in time → `408`.
- `GET /status`: mouth mode; Mumble connected or not; per motor the
  current command, present volts, whether max hold has tripped, whether it
  is calibrated, whether its range is two-sided, and its pose names.
- `GET /`: the control page — a slider per motor, pose buttons, the
  live/show switch and a status panel refreshed every second. A held
  slider resends its value about 20 times a second (dead-man); releasing
  it lets the motor rest. One self-contained HTML file
  (`jack/adapters/network/control_page.html`), no external scripts.
- On macOS 15+, the browser may need Local Network permission to reach
  `http://10.10.0.54:8080`, as the Mumble client did.

Both protocols go through one pure translation module,
`jack/show/control/show_commands.py`, which turns an OSC address + arguments or an
HTTP path + JSON body into board commands; all validation and clamping
live there, so OSC and HTTP behave identically.

### Calibration for every motor

`calibrate.py <motor>` (e.g. `calibrate.py elbow`) keeps today's
`<volts> <seconds>` lines and safety limits for any of the four motors; its
named shortcuts are that motor's poses from `poses.toml`. It still refuses
to run while `jack.service` is active.

### Board check (one-off, not product code)

With nothing wired to the second HAT, drive each of its channels in turn
at a known voltage while Boss reads MA1/MA2 and MB1/MB2 with a meter.

### Testing

- Mac (pytest): registry and `poses.toml` loading (missing fields, unknown
  motor, bad rest mode, volts beyond supply rejected; hold lookup at,
  between and below points and per direction; each unmeasured-hold
  refusal and a leftover `max_hold_s`; the repo file loads with every pose
  within its hold); command board timing (dead-man, pose duration and
  override, replacement, max hold trip at 6 V and at 2 V, a mixed-voltage
  drive sharing one budget, refill at rest, rest pulse, slew); translation of every OSC address and HTTP
  route incl. clamping and unknown names; HTTP against a real server on a
  localhost port; OSC end to end with real UDP packets on localhost; talk
  loop driving all four motors from the board, the mode switch, and all
  four braked on exit.
- Pi: the board check; `calibrate.py` for the elbow to replace its
  placeholders; OSC from Boss's show controller and the control page in a
  browser; the recovery checks again with four motors.

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

### Package layout (Boss, 2026-10-03)

The code lives in one package, `jack/` (formerly `motor_test/`), grouped by
hexagonal layer, then by function:

```
jack/
  show/                 pure logic: no hardware, network, sound card or systemd
    audio/      pcm, envelope, frame_queue, lip_sync, talk_settings
    motion/     motors, poses, motor_driver, stall_budget, ramp, mouth, speech
    control/    control_board, show_commands, osc_feedback
  application/  ports, talk_loop, calibration, smoke_test
  adapters/
    hardware/   pca9685, tb6612_motor
    audio/      alsa_sink, mumble_voice, wav_source
    network/    osc_server, http_server, control_page.html
    system/     systemd_notify, service_guard
  support/      attempt_all, rate_limited_log
```

- `show/` is Jack's show itself: what the motors, voice and controls mean,
  with no I/O. One exception: `show/motion/poses.py` reads its TOML files,
  since loading and validating them is one job.
- `application/` runs the show: the talk loop and the tools' logic,
  talking to the outside only through the `Protocol`s in
  `application/ports.py`.
- `adapters/` are the only modules that touch hardware, the network, the
  sound card or systemd.
- `support/` holds small helpers any layer may use.
- Imports only point down or sideways: `show/` imports only `show/` and
  `support/`; `application/` adds `show/`; `adapters/` may import any
  layer; `support/` imports only `support/`. A test reads every module's
  imports and fails on one that points the wrong way. (`talk_settings`
  sits in `show/audio/` because `lip_sync` reads it.)
- Every directory is a package (`__init__.py`, empty). Modules are imported
  by full path, e.g. `from jack.show.motion.poses import load_profiles`.
- The entry scripts stay at the repo root (`main.py`, `calibrate.py`,
  `lipsync_wav.py`) with `poses.toml` and `tools/`, so `jack.service`
  (`/opt/jack/main.py`) and the deploy are unchanged. Tests stay flat in
  `tests/`.
- Files move with `git mv` so their history follows. Plans under `docs/`
  keep their `motor_test` paths as a record of what was built then.

Show control's modules (`motors.py`, `poses.py`, `poses.toml`,
`control_board.py`, `show_commands.py`, `osc_server.py`, `http_server.py`,
`control_page.html`) are described in "Show control". Two more modules
support them:

- `jack/show/motion/motor_driver.py` (domain, pure): `MotorDriver`, one per
  motor, turns the commanded volts (or None for rest) into its drive each
  20 ms tick: slew limit on driving harder, max-hold trip, rest pulse, then
  `Rest("brake")` or `Rest("coast")`.
- `jack/support/rate_limited_log.py`: `RateLimitedLog(print, interval_s,
  clock)`, a callable that lets one line per key through per interval, so a
  flood of bad OSC messages stays one journal line per kind per minute.

- `jack/show/motion/ramp.py` (domain, pure): waveforms as signed 12-bit duty
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
- `jack/show/motion/mouth.py` (domain, pure): the calibrated mouth poses as
  `(start_volts, end_volts, seconds)` segments built with `hold()` and
  `ramp()`: `CLOSE`, `RELAX`, `open_fully(seconds)` (ramps over
  `OPEN_RAMP_S` = 0.25 s, then holds) and `rest(seconds)`, plus
  `describe()` for log lines.
- `jack/application/ports.py`: the `MotorOutput` protocol, with
  `drive(count)` (signed) and `stop()`.
- `jack/adapters/hardware/pca9685.py` (adapter): `Pca9685(bus, address, pwm_freq_hz)`,
  the PWM chip, created once per HAT (`0x40`, `0x41`) and shared by that HAT's two motors. Register logic
  follows Waveshare's `PCA9685.py`, writing 12-bit counts directly
  (Waveshare's `setDutycycle` scales by 40 and never reaches the full
  4096). `set_off_count(channel, count)` rejects counts outside 0..4095.
- `jack/adapters/hardware/tb6612_motor.py` (adapter): `Tb6612Motor(chip, channels,
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
- `jack/support/attempt_all.py`: `attempt_all(actions)` runs every action
  even if an earlier one raises, then re-raises the first failure. Used
  wherever braking must not be skipped.
- `jack/show/motion/speech.py` (domain): `random_phrase(rng)` builds one
  talking phrase from the calibrated poses (see "Smoke test behavior");
  pass a seeded `random.Random` for repeatable output.
- `jack/application/smoke_test.py` (application): `run_generated_loop(motors,
  next_cycle, step_s, sleep, on_cycle)` asks `next_cycle()` for one count
  list per motor each cycle and plays them in lockstep: step i drives
  every motor with its i-th count, then waits `step_s`. It repeats forever,
  calls `on_cycle()` after each completed cycle, and always stops every
  motor when the loop exits (exception, unplayable cycle or SIGTERM), even
  if stopping one fails. `run_profiles_loop(profiles, ...)` is the
  fixed-profile form, checked before any motor is touched.
- `jack/adapters/system/systemd_notify.py` (adapter): `notify(message)` sends one
  sd_notify datagram to `$NOTIFY_SOCKET` using only the standard library.
  It does nothing when `NOTIFY_SOCKET` is unset (running by hand), and
  supports abstract-namespace sockets (`@` prefix).
- `jack/show/audio/pcm.py` (domain, pure): the frame format (48 kHz mono
  16-bit, 20 ms = 960 samples), `silence()` and `mix(frames)`.
- `jack/show/audio/envelope.py` (domain, pure): frame RMS in dBFS and the
  attack/release smoother.
- `jack/show/audio/talk_settings.py` (domain, pure): `TalkSettings`, every
  tunable in the table above with its starting value, validated.
- `jack/show/audio/lip_sync.py` (domain, pure): the mouth state machine; level
  in, signed volts out, one call per tick.
- `jack/show/audio/frame_queue.py` (domain): `FrameQueue`, a thread-safe,
  bounded queue that cuts arbitrary PCM chunks into frames and drops the
  oldest past its limit.
- `jack/application/ports.py` gains `VoiceSource` (`take_frames()`: the next
  frame from each voice currently sounding) and `AudioSink` (`write(frame)`,
  blocking; `close()`).
- `jack/adapters/audio/mumble_voice.py` (adapter): a `VoiceSource` with one
  `FrameQueue` per Mumble talker, fed by pymumble's sound callback, plus
  `connect_mumble()`. The only module that imports pymumble.
- `jack/adapters/audio/roc_voice.py` (adapter): `roc_recv_command(...)` and
  `RocVoice(command, max_backlog_frames, log)`, a `VoiceSource` that runs
  and supervises `roc-recv`, keeps the left channel of its raw stereo
  output, and skips digital silence (see "ROC voice").
- `jack/adapters/audio/wav_source.py` (adapter): a `VoiceSource` playing one WAV
  file, a frame per tick.
- `jack/adapters/audio/alsa_sink.py` (adapter): an `AudioSink` on ALSA card 0 via
  `python3-alsaaudio`, riding through underruns. The only module that
  imports `alsaaudio`.
- `jack/adapters/system/service_guard.py`: `app_is_running()`, shared by
  `calibrate.py` and `lipsync_wav.py`.
- `jack/application/talk_loop.py` (application): `run_talk_loop`, taking the
  sources, an `AudioSink`, the motors by name, their profiles, the settings,
  the `ControlBoard`, the supply voltage, an `on_second` callback and an
  `until` check; always brakes every motor and closes the sink on exit.
- `lipsync_wav.py`: composition root for the tuning tool (a `WavSource`
  in place of Mumble).
- `main.py`: builds the `TalkSettings` with `talk_settings()` (defaults plus
  `JACK_<FIELD>` overrides from the environment, checked against the supply
  before touching hardware; the mouth's old volt variables are refused),
  loads every motor's profile from `poses.toml` with `motor_profiles()`
  (`POSES_PATHS`: the repo file, then `/etc/jack/poses.toml`), and reads the
  ports, dead-man timeout and startup mouth mode with
  `show_control_config()` (`JACK_OSC_PORT`, `JACK_HTTP_PORT`,
  `JACK_CONTROL_TIMEOUT_S`, `JACK_MOUTH_MODE`). Before all of that it installs a SIGTERM
  handler that raises `SystemExit` so the loop's cleanup runs. Once the
  settings, profiles and show-control config are validated it builds the
  `ControlBoard`, connects the Mumble bot, then builds both HATs and all four
  motors with `build_motors` (each HAT's motors braked as soon as it is up; a
  HAT that does not answer stops the app naming its address), opens the
  ALSA sink, starts the OSC (UDP) and HTTP (TCP) servers on `0.0.0.0`, sends
  `READY=1`, and runs the talk loop with `on_second`
  the Mumble-aware watchdog callback (`WATCHDOG=1` while the bot's thread
  lives, `SystemExit` once it has died). `run_profiles_loop` and
  `MOUTH_DEMO` are no longer used by `main.py`; they stay in the repo
  (Boss, 2026-09-28).
- `speaking_cycle(rng)` and `demo_cycle()` remain in `main.py` (Boss, 2026-09-28) for showing the mechanism without audio; `main()` no longer uses them.

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
  - `main.demo_cycle` holds motor B at 0 V (the retired demo; in the app
    motor B is the hand, driven by show control) and plays the mouth demo
    (+341 ×5, 0 ×30, −683 ×10, 0 ×30, the 5-step ramp, −2048 ×10) over a
    4.5 s cycle, inside the 10 s watchdog with two cycles to spare.
  - `main.speaking_cycle` plays `random_phrase` on the mouth with motor B
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
    close, and the stall budget closing a long opening, holding it closed
    until a pause, and refilling at every close.
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
- Dependencies for tests: `requirements-dev.txt` pins `pytest==9.1.1`,
  `smbus2==0.4.3` and `python-osc==1.10.2`.
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
  `python3-opuslib` and `python3-protobuf` are used. Only pymumble (and,
  with show control, `python-osc`) comes
  from pip, pinned in `requirements-pi.txt` to the exact version or git
  commit the spike proved (`python-osc==1.10.2`, also pinned in
  `requirements-dev.txt` for the tests), and installed with `--no-deps` (pymumble pins
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
- Authentication for show control (LAN trusted; Boss, 2026-10-02).
- Persisting the mouth mode across restarts.
- Scripted gestures or animation sequences inside Jack: movement beyond
  the mouth's lip sync comes from the show controller.
- The TB6612FNG `STBY` pin. Waveshare's sample code never drives it.
- Push-based deploys (webhooks, self-hosted runners).
