# Jack: Raspberry Pi Motor HAT App with Git-Driven Deploys

## Purpose

The Raspberry Pi `jack` (10.10.0.54) runs `main.py` from this repository
as a boot-time system service. When `main` on GitHub changes, the Pi pulls
the new commit and restarts the app automatically. The initial `main.py` is
a hardware smoke test for the Waveshare Motor Driver HAT that proves the
whole chain works: push → Pi pulls → app restarts → motor output moves.

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

## Smoke test behavior (`main.py`)

Purpose: find which voltages move the animatronic head's mouth (driven by
motor B) and how.

1. Drive both motor channels in lockstep in 50 ms steps. Each motor has
   its own signed profile; positive means forward (IN1 = 0, IN2 = 1, so
   terminal 1 is positive), negative means backward (IN1 = 1, IN2 = 0).
2. **Motor B** holds −6 V for 0.5 s, then −3 V for 1 s, then 0 V for 2 s
   (3.5 s cycle), set in `MOTOR_B_SEQUENCE` as `(volts, seconds)` pairs.
   - With 12 V supply, 6 V = 50% duty = count 2048; 3 V = count 1024.
3. **Motor A** holds 0 V for the whole cycle.
4. Repeat the cycle back-to-back, with no pause, until the process is
   stopped.
5. On SIGTERM or any exception, **short-brake** both motors before exiting:
   duty 0, then IN1 = IN2 = high (the TB6612FNG shorts the motor leads).
   A failure braking one motor must not prevent braking the other.

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
  and `WATCHDOG=1` after every completed 2 s ramp cycle. If no ping
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
  `hold_sequence_profile(segments, supply_volts, step_s)` holds each
  `(volts, seconds)` segment in order and rejects durations that are not a
  whole number of steps. All raise `ValueError` for a non-positive supply, a voltage outside the
  supply range, or an odd or too-small step count. Magnitudes cap at 4095
  (4096 would set the PCA9685 full-off bit).
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
  pins only when the direction changes. `stop()` short-brakes.
  `reverse=True` swaps the direction for a motor wired with flipped
  polarity. Signed drive, short brake and reverse are borrowed from
  https://github.com/nick-hunter/Raspberry_Pi_TB6612FNG_Python (MIT). That
  library drives TB6612 pins from Pi GPIO, which this HAT does not do, so
  only the ideas are borrowed.
- `motor_test/attempt_all.py`: `attempt_all(actions)` runs every action
  even if an earlier one raises, then re-raises the first failure. Used
  wherever braking must not be skipped.
- `motor_test/smoke_test.py` (application): `run_profiles_loop(profiles,
  step_s, sleep, on_cycle)` takes `(motor, counts)` pairs of equal length.
  Step i drives every motor with its i-th count, then waits `step_s`; it
  repeats forever, calls `on_cycle()` after each completed cycle, and
  always stops every motor when the loop exits (exception or SIGTERM),
  even if stopping one fails.
- `motor_test/systemd_notify.py` (adapter): `notify(message)` sends one
  sd_notify datagram to `$NOTIFY_SOCKET` using only the standard library.
  It does nothing when `NOTIFY_SOCKET` is unset (running by hand), and
  supports abstract-namespace sockets (`@` prefix).
- `main.py`: builds one `Pca9685`, pairs motor A (0 V) and motor B (its hold
  sequence) with their profiles (`build_profiles`), wires them to the profile loop,
  installs a SIGTERM handler that
  raises `SystemExit` so the loop's cleanup runs, sends `READY=1` after
  motor init, and starts the loop with `on_cycle` sending `WATCHDOG=1`.

## Testing

- pytest, run on the dev Mac (no hardware needed) for the domain and
  application layers:
  - Ramp starts at 0, peaks at 2048 for 6 V/12 V at index `steps // 2`,
    is symmetric, and has the requested number of steps. A full-supply
    peak caps at 4095. Square is +1024 for the first half and −1024 for
    the second for ±3 V/12 V, and caps at ±4095. Constant −6 V/12 V is
    −2048 at every step. The −6/−3/0 V hold sequence at 50 ms steps is 10,
    20 and 40 steps.
  - Invalid inputs raise `ValueError`.
  - `run_profiles_loop` drives each motor with its own profile in
    lockstep, repeats the cycle, stops every motor when interrupted
    (even if one stop fails), and rejects empty or mismatched profiles
    (the test's `sleep` raises after N steps to end the loop). This uses a
    recording fake `MotorOutput`, which checks our sequencing, not a
    mock's behavior.
  - `run_profiles_loop` calls `on_cycle` exactly once per completed cycle.
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
  - `main.build_profiles` holds motor A at 0 V and plays motor B's
    −6/−3/0 V sequence over a 3.5 s cycle, well inside the 10 s watchdog.
- The adapters are verified on real hardware: after install, watch
  `journalctl -u jack` and measure across MA1/MA2 and MB1/MB2 with a
  meter.

## Deployment

All units are system services in `/etc/systemd/system`. They start at boot
and are not tied to any login session or human user.

- **Checkout:** `/opt/jack`, owned by root, cloned from
  `https://github.com/robbiebyrd/jack.git` (public, no credentials).
- **`jack.service`:** runs `/usr/bin/python3 /opt/jack/main.py` as the
  dedicated system account `jack` (no home, nologin shell, only in group
  `i2c`). Self-recovery settings as above; `WantedBy=multi-user.target`.
- **`jack-update.service` + `jack-update.timer`:** runs as root every 60 s
  (`OnBootSec=60`, `OnUnitActiveSec=60`). Executes
  `/opt/jack/deploy/jack-update.sh`.
- **`deploy/jack-update.sh`:** `git fetch origin main`; if `HEAD` differs
  from `origin/main`, `git reset --hard origin/main` and
  `systemctl try-restart jack` (restarts only if running, so a deliberate
  stop is not overridden). Local edits on the Pi are discarded by design.
  The repo is the only source of truth.
- **`deploy/install.sh`** (run once as root by Boss): installs `git`,
  enables I2C (`raspi-config nonint do_i2c 0`), creates the `jack` system
  user, clones to `/opt/jack`, installs and enables the units, and says
  to reboot so I2C takes effect.
- **Logs:** `journalctl -u jack -u jack-update`.
- **Accepted risk:** `jack-update.service` runs
  `/opt/jack/deploy/jack-update.sh` as root, and that script is replaced
  from the repo on every deploy, so anyone who can push to `main` can run
  code as root on the Pi. Boss accepted this deliberately in exchange for
  the updater updating itself.

## Out of scope

- Real application behavior beyond this smoke test.
- The TB6612FNG `STBY` pin. Waveshare's sample code never drives it.
- Push-based deploys (webhooks, self-hosted runners).
