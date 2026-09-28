# jack

Raspberry Pi + Waveshare Motor Driver HAT driving an animatronic head's mouth
(motor B). `main.py` repeats motor B's `MOTOR_B_SEQUENCE` (−6 V for 0.5 s, −3 V
for 1 s, 0 V for 2 s) with motor A off, and short-brakes both on exit. See `SPEC.md`.

## Install on the Pi (once)

```bash
scp deploy/install.sh 10.10.0.54:
ssh -t 10.10.0.54 sudo bash install.sh
```

This installs git, enables I2C, creates the `jack` system user, clones to
`/opt/jack`, and enables `jack.service` and `jack-update.timer`.

## Deploys

Push to `main`. Within about 60 s the Pi runs `git reset --hard origin/main`
in `/opt/jack` and restarts `jack.service`. Edits made on tracked files on the
Pi are discarded; `git reset --hard` leaves untracked files in place.
Changes to the unit files in `deploy/` are not reinstalled automatically.
Re-run `install.sh` for those. A re-run does not restart an already-running
app, so follow it with `sudo systemctl try-restart jack` to pick up unit
changes.

## Self-recovery

`jack.service` restarts after any exit (`Restart=always`, 5 s delay, never
gives up) and is watched by the systemd watchdog. If the loop stops pinging
for 10 s, systemd kills and restarts it.

## Calibrate the mouth

```bash
ssh -t 10.10.0.54 sudo systemctl stop jack   # free the HAT (brakes both motors)
ssh -t 10.10.0.54 python3 /opt/jack/calibrate.py
b> -4.5 0.8                                  # motor B at -4.5 V for 0.8 s, then brake
b> q
ssh -t 10.10.0.54 sudo systemctl start jack  # resume the sequence
```

Negative volts open the mouth (0 V closed, about −6 V open). Moves are capped
at 3 s and always end braked.

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
