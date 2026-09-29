# jack

Raspberry Pi + Waveshare Motor Driver HAT driving an animatronic head's mouth
(motor B). `main.py` has Jack talk: it joins a Mumble server, plays the voice on the
3.5 mm jack and moves the mouth with its loudness, with motor A off, and
short-brakes both on exit. See `SPEC.md`.

## Install on the Pi (once)

```bash
scp deploy/install.sh 10.10.0.54:
ssh -t 10.10.0.54 sudo bash install.sh
```

This installs git, mumble-server and the audio packages, enables I2C, creates the `jack` system
user and the pymumble venv (`/opt/jack-venv`), clones to `/opt/jack`, creates `/etc/jack/jack.env`,
and enables `jack.service` and `jack-update.timer`.

## Deploys

Push to `main`. Within about 60 s the Pi runs `git reset --hard origin/main`
in `/opt/jack` and restarts `jack.service`. Edits made on tracked files on the
Pi are discarded; `git reset --hard` leaves untracked files in place.
Changes to the unit files in `deploy/` are not reinstalled automatically.
Re-run `install.sh` for those. A re-run restarts the app (`systemctl restart`)
so a changed unit takes effect.

## Self-recovery

`jack.service` restarts after any exit (`Restart=always`, 5 s delay, never
gives up) and is watched by the systemd watchdog. If the loop stops pinging
for 10 s, systemd kills and restarts it.

## Calibrate the mouth

```bash
ssh -t 10.10.0.54 sudo systemctl stop jack   # free the HAT (brakes both motors)
ssh -t 10.10.0.54 python3 /opt/jack/calibrate.py
b> -4.5 0.8                                  # motor B at -4.5 V for 0.8 s, then brake
b> close                                     # also: relax, open 0.5
b> q
ssh -t 10.10.0.54 sudo systemctl start jack  # resume the sequence
```

Negative volts open the mouth (0 V closed, about −6 V open). `open` ramps up
to −6 V over 0.25 s. Moves run in 50 ms steps (durations must be whole steps),
are capped at 3 s, and always end braked.

After each deploy, the Pi's green and red onboard LEDs blink for 10 s.

## Talking

Jack plays whatever is said in its Mumble server's root channel and moves the mouth with it.

1. Install the Mumble desktop client on your computer and connect to `10.10.0.54`,
   port `64738`, with the server password. Use push-to-talk.
2. The server password is set on the Pi in `/etc/mumble/mumble-server.ini` (`serverpassword=`) and
   `/etc/jack/jack.env` (`JACK_MUMBLE_PASSWORD=`); both must match. After changing them:
   `sudo systemctl restart mumble-server jack`.

### Tuning the lip sync

Quickest: override any setting on the Pi. Add `JACK_<SETTING_NAME>` lines to `/etc/jack/jack.env`
(names are the fields of `motor_test/talk_settings.py`, upper-case) and restart:

```bash
# /etc/jack/jack.env
JACK_GATE_OPEN_DB=-20
JACK_OPEN_CURVE=1.5
```

```bash
sudo systemctl restart jack
journalctl -u jack -n 20   # an invalid value stops the app with a one-line message here
```

Or try a WAV offline. Record a WAV of speech, convert it to mono 16-bit 48 kHz on the Mac, copy it to the Pi and play it
through the same loop, overriding any setting from `motor_test/talk_settings.py`:

```bash
afconvert -f WAVE -d LEI16@48000 -c 1 speech.m4a speech.wav
scp speech.wav 10.10.0.54:/tmp/
ssh -t 10.10.0.54 'sudo systemctl stop jack && /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py /tmp/speech.wav --release-s 0.1'
```

Run with `--help` for every setting. When you like a set of values, put them in `/etc/jack/jack.env` (or make them the defaults in
`motor_test/talk_settings.py`), then `sudo systemctl start jack`.

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
