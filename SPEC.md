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

1. On start, drive motor B (terminals MB1 +, MB2 −) forward
   (BIN1 = 0, BIN2 = 1).
2. Ramp average voltage 0 V → 6 V over 1 s, then 6 V → 0 V over 1 s
   (2 s total), in 50 evenly spaced steps (40 ms each).
   - With 12 V supply, 6 V = 50% duty = PCA9685 count 2048 of 4096.
3. Repeat the 2 s cycle back-to-back, with no pause, until the process
   is stopped.
4. On SIGTERM or any exception, set motor B duty to 0 before exiting.

## Code structure (hexagonal)

- `motor_test/ramp.py` (domain, pure): `ramp_profile(peak_volts,
  supply_volts, duration_s, steps)` returns the list of 12-bit duty
  counts for an up-then-down triangle ramp. It raises `ValueError` if the
  peak is above the supply or negative, or if the supply is not positive.
- `motor_test/ports.py`: the `MotorOutput` protocol, with `set_forward()`,
  `set_duty(count)` and `stop()`.
- `motor_test/pca9685_motor.py` (adapter): `MotorOutput` for one
  TB6612FNG channel through the PCA9685 over smbus2. Register logic follows
  Waveshare's `PCA9685.py`, writing 12-bit counts directly (Waveshare's
  `setDutycycle` scales by 40 and never reaches the full 4096).
- `motor_test/smoke_test.py` (application): `run_ramp_loop(motor,
  counts, step_s, sleep)` sets direction, plays the counts cycle after
  cycle forever, and always stops the motor when the loop exits (exception
  or SIGTERM).
- `main.py`: wires the adapter and ramp, installs a SIGTERM handler that
  raises `SystemExit` so the loop's cleanup runs, and starts the loop.

## Testing

- pytest, run on the dev Mac (no hardware needed) for the domain and
  application layers:
  - Ramp starts and ends at 0, peaks at 2048 for 6 V/12 V, is symmetric,
    has the requested number of steps.
  - Invalid inputs raise `ValueError`.
  - `run_ramp_loop` sets forward before driving, plays every count in
    order, repeats the cycle, and stops the motor when interrupted
    (the test's `sleep` raises after N steps to end the loop). This uses a recording fake
    `MotorOutput`, which checks our sequencing, not a mock's behavior.
- The PCA9685 adapter is verified on real hardware: after install, watch
  `journalctl -u jack` and measure across MB1/MB2 with a meter.

## Deployment

All units are system services in `/etc/systemd/system`. They start at boot
and are not tied to any login session or human user.

- **Checkout:** `/opt/jack`, owned by root, cloned from
  `https://github.com/robbiebyrd/jack.git` (public, no credentials).
- **`jack.service`:** runs `/usr/bin/python3 /opt/jack/main.py` as the
  dedicated system account `jack` (no home, nologin shell, only in group
  `i2c`). `Restart=on-failure`, `WantedBy=multi-user.target`.
- **`jack-update.service` + `jack-update.timer`:** runs as root every 60 s
  (`OnBootSec=60`, `OnUnitActiveSec=60`). Executes
  `/opt/jack/deploy/jack-update.sh`.
- **`deploy/jack-update.sh`:** `git fetch origin main`; if `HEAD` differs
  from `origin/main`, `git reset --hard origin/main` and
  `systemctl restart jack`. Local edits on the Pi are discarded by design.
  The repo is the only source of truth.
- **`deploy/install.sh`** (run once as root by Boss): installs `git`,
  enables I2C (`raspi-config nonint do_i2c 0`), creates the `jack` system
  user, clones to `/opt/jack`, installs and enables the units, and says
  to reboot so I2C takes effect.
- **Logs:** `journalctl -u jack -u jack-update`.

## Out of scope

- Motor A, real application behavior, speed or direction control APIs.
- Push-based deploys (webhooks, self-hosted runners).
