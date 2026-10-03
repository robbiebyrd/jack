# jack

Raspberry Pi + two Waveshare Motor Driver HATs driving an animatronic's four motors: mouth,
hand, pivot and elbow. `main.py` has Jack talk: it joins a Mumble server, plays the voice on the
3.5 mm jack and moves the mouth with its loudness, and takes show control over OSC and HTTP for
all four motors. Every motor is braked at startup and on exit. See `SPEC.md`.

## Install on the Pi (once)

```bash
scp deploy/install.sh 10.10.0.54:
ssh -t 10.10.0.54 sudo bash install.sh
```

This installs git, mumble-server and the audio packages, enables I2C, creates the `jack` system
user and the Python venv for pymumble and python-osc (`/opt/jack-venv`), clones to `/opt/jack`, creates `/etc/jack/jack.env`,
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

## Calibrate a motor

```bash
ssh -t 10.10.0.54 sudo systemctl stop jack   # free the HATs (brakes all four motors)
ssh -t 10.10.0.54 sudo -u jack /opt/jack-venv/bin/python /opt/jack/calibrate.py mouth   # or hand, pivot, elbow
mouth> -4.5 0.8                              # at -4.5 V for 0.8 s, then brake
mouth> close                                 # a pose from poses.toml; also: relax, open 0.5
mouth> q
ssh -t 10.10.0.54 sudo systemctl start jack  # resume
```

Positive volts open the mouth (0 V closed, about +6 V open). Poses ramp up from 0 V at the
motor's `slew_v_per_s`, then hold. Moves run in 50 ms steps (durations must be whole steps),
are capped at 3 s, and always end braked. The tool lists the motor's poses when it starts.
Run it as the `jack` user: only `jack` can read `/etc/jack`, where the motor overrides live.

To measure a new motor (hand, pivot or elbow, whose entries in `poses.toml` are uncalibrated
placeholders), stop the app and run `calibrate.py` with its name, then put the volts you find in
`/etc/jack/poses.toml`:

```bash
sudo systemctl stop jack
sudo -u jack /opt/jack-venv/bin/python /opt/jack/calibrate.py elbow
```

After each deploy, the Pi's green and red onboard LEDs blink for 10 s.

## Talking

Jack plays whatever it hears over ROC (from a Mac) or in its Mumble server's root channel, and moves the
mouth with it. Both work at once; ROC is the everyday way, Mumble the fallback.

1. Install the Mumble desktop client on your computer and connect to `10.10.0.54`,
   port `64738`, with the server password. Use push-to-talk.
2. The server password is set on the Pi in `/etc/mumble/mumble-server.ini` (`serverpassword=`) and
   `/etc/jack/jack.env` (`JACK_MUMBLE_PASSWORD=`); both must match. After changing them:
   `sudo systemctl restart mumble-server jack`.

### Talking over ROC (from a Mac)

1. Install the Roc Virtual Audio Device on the Mac (the installer may report errors on `/usr/` and
   `/Library/` while still installing both files), then reboot:
   `sudo /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/roc-streaming/roc-vad/HEAD/install.sh)"`
2. Check it: `roc-vad info` says `driver is loaded`.
3. Create a sender and point it at the Pi (use the id `roc-vad device list` shows):
   ```bash
   roc-vad device add sender --name "Jack"
   roc-vad device connect 1 \
     --source rtp+rs8m://10.10.0.54:10001 \
     --repair rs8m://10.10.0.54:10002 \
     --control rtcp://10.10.0.54:10003
   ```
4. Choose "Jack" as the Mac's sound output (System Settings → Sound → Output). Anything the Mac
   plays comes out of Jack, about 0.1 s later; you won't hear it on the Mac.

If the sound breaks up, raise `JACK_ROC_TARGET_LATENCY_MS` (below); on the home Wi-Fi 90 ms held
and 60 ms broke up.

### Tuning the lip sync

Quickest: override any setting on the Pi. Add `JACK_<SETTING_NAME>` lines to `/etc/jack/jack.env`
(names are the fields of `jack/show/audio/talk_settings.py`, upper-case) and restart. The mouth's
volts are not among them: `min_v`, `max_v`, `slew_v_per_s`, `rest_pulse_v` and `rest_pulse_s` are
tuned in `/etc/jack/poses.toml` under `[mouth]` (the app refuses to start if the old
`JACK_OPEN_MIN_V`, `JACK_OPEN_MAX_V`, `JACK_OPEN_SLEW_V_PER_S`, `JACK_CLOSE_V` or `JACK_CLOSE_S` is
still set):

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
through the same loop, overriding any setting from `jack/show/audio/talk_settings.py`:

```bash
afconvert -f WAVE -d LEI16@48000 -c 1 speech.m4a speech.wav
scp speech.wav 10.10.0.54:/tmp/
ssh -t 10.10.0.54 'sudo systemctl stop jack && sudo -u jack /opt/jack-venv/bin/python /opt/jack/lipsync_wav.py /tmp/speech.wav --release-s 0.1'
```

Run with `--help` for every setting. When you like a set of values, put them in `/etc/jack/jack.env` (or make them the defaults in
`jack/show/audio/talk_settings.py`), then `sudo systemctl start jack`. Mouth volts go in `/etc/jack/poses.toml` `[mouth]`.

## Show control

An external show controller (QLab, a lighting desk, TouchDesigner, a script) commands the four
motors (`mouth`, `hand`, `pivot`, `elbow`) over OSC (UDP, port 9000) or HTTP (TCP, port 8080).
The voice keeps playing in every mode. There is no authentication: anyone on the LAN can send
commands.

Keep sending values: a value holds only while new ones keep arriving, and 0.5 s of silence rests
the motor (`JACK_CONTROL_TIMEOUT_S`).

### OSC

| Address | Arguments | Effect |
|---|---|---|
| `/jack/<motor>` | float | Continuous value (0…1; hand and pivot −1…1) |
| `/jack/<motor>/pose` | string, optional float | Named pose, optional duration (s) |
| `/jack/<motor>/pose/<name>` | none, or a button value | That pose for its default duration |
| `/jack/<motor>/rest` | none, or a button value | That motor to rest |
| `/jack/rest` | none, or a button value | All motors to rest |
| `/jack/mouth/mode` | `live` or `show`, or 1/0 | Switch the mouth's source |
| `/jack/mouth/mode/show` | 1 or 0 | `show` for non-zero, `live` for 0 |

Out-of-range values are clamped. Unknown motors, poses or addresses are ignored, and logged at
most once a minute per kind (`journalctl -u jack`).

### OSC replies and feedback

Jack answers on the same UDP socket it listens on, so replies pass through NAT and firewalls that
let the command through.

| Address | Arguments | Effect |
|---|---|---|
| `/jack/ping` | none | Replies `/jack/pong` |
| `/jack/status` | none | Replies with every state message below, once |
| `/jack/subscribe` | optional int port | Sends state messages to the sender (or that port) for 60 s |
| `/jack/unsubscribe` | optional int port | Stops them |

Replies and feedback leave from Jack's port 9000 and go to the sender's IP and source port, which
the controller's firewall or NAT usually lets through. Set `JACK_OSC_REPLY_PORT` in
`/etc/jack/jack.env` to send them to a fixed port instead (for a controller that listens on a
different port than it sends from); a port argument to `/jack/subscribe` overrides both. With
either, the controller machine must allow inbound UDP on that port.

Port arguments are OSC ints, or whole-number floats (`21601.0` means 21601), which is what
TouchOSC sends. A fractional port, or one outside 1024-65535, is rejected with a logged "Ignored OSC … " and no
feedback. `/jack/unsubscribe` must carry the same port argument as the `/jack/subscribe` it
undoes (or none, if none was used), otherwise it silently does nothing. OSC is UDP only, no TCP.

A subscription lasts 60 s: renew it by sending `/jack/subscribe` again at least every 60 s, or it
lapses. At most 8 subscribers are served. A subscriber gets every state message on subscribing and
renewing and once a second after that, and in between only the ones that changed.

| State message | Value |
|---|---|
| `/jack/<motor>` | float, the motor's current value command (0 at rest or during a pose) |
| `/jack/<motor>/volts` | float, the volts Jack last drove |
| `/jack/<motor>/max_hold` | int 0 or 1, whether the max hold tripped |
| `/jack/mouth/mode` | string, `live` or `show` |
| `/jack/mouth/mode/show` | float, 1.0 in `show`, 0.0 in `live` |
| `/jack/mumble` | int 0 or 1, whether Mumble is connected |

Subscribe from a script and print what arrives (`pip install python-osc`; here the script listens
on UDP 9001):

```python
import threading
import time
from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

dispatcher = Dispatcher()
dispatcher.set_default_handler(lambda address, *args: print(address, args))
server = ThreadingOSCUDPServer(("0.0.0.0", 9001), dispatcher)
client = SimpleUDPClient("10.10.0.54", 9000)

threading.Thread(target=server.serve_forever, daemon=True).start()
while True:
    client.send_message("/jack/subscribe", 9001)  # renew well inside 60 s
    time.sleep(30)
```

### TouchOSC

**Ready-made layout:** `touchosc/jack.tosc` has every control. Open it in TouchOSC (made for
TouchOSC 1.x, checked with py2tosc's validator but not yet opened in TouchOSC itself). It has:

- a header row: Subscribe, Ping, a Mumble light, the Mouth show/live toggle and REST ALL;
- one column per motor: a fader, a volts readout, a MAX HOLD light, a button per pose and Rest.

In TouchOSC's connection settings set host `10.10.0.54`, send port `9000` and receive port
`21601`, then press Subscribe. The layout is generated from `poses.toml`, so after calibrating or
renaming poses, regenerate it (on the Mac):

```bash
.venv/bin/python -m tools.touchosc_layout                 # receive port 21601
.venv/bin/python -m tools.touchosc_layout --port 9001     # another receive port
```

Jack accepts what TouchOSC sends without scripting in the layout. This section is based on
general TouchOSC behaviour (buttons send a number, faders send floats, controls update when a
message arrives on their own address). It has not been tested against a specific TouchOSC
version, so check it against your layout.

- **Connection:** host `10.10.0.54`, send port `9000`, receive port your choice (for example
  `21601`).
- **Subscribe button:** send `/jack/subscribe` with your receive port as its value (int or whole
  float, at least 1024) as its on-value; its release is ignored. Once subscribed, any command you
  send keeps the subscription alive, so a layout in use stays subscribed; otherwise press it again
  within 60 s. Or set `JACK_OSC_REPLY_PORT` (see above) and send no value. A press arrives as a
  number, so the value, not the button, carries the port.
- **Faders:** `/jack/elbow` and `/jack/mouth` use 0 to 1. `/jack/hand` (open … curled) and
  `/jack/pivot` (left … right) use -1 to 1, so set those faders' range to -1...1. Whether a held fader keeps resending while
  still is unverified. If it doesn't, a fader held still longer than the 0.5 s dead-man rests the
  motor and its feedback drops to 0; raise `JACK_CONTROL_TIMEOUT_S` if that bites (each motor's
  holds still cap any hold). Whether TouchOSC re-sends values it receives is also unverified.
  Releasing a fader lets the motor rest after 0.5 s. `/jack/mouth` only works in `show` mode.
- **Buttons:** `/jack/rest`, `/jack/<motor>/rest` and `/jack/<motor>/pose/<name>` (for example
  `/jack/elbow/pose/up`) act on press (a non-zero number) and ignore release (0). The pose plays
  for its default duration; an unknown pose is logged and ignored. The same press/release rule
  applies to `/jack/ping` and `/jack/status`.
- **Mouth mode toggle:** a toggle button on `/jack/mouth/mode/show`: on is `show`, off is
  `live`. It must not be a momentary button, because its release (0) switches back to `live`.
  `/jack/mouth/mode` also accepts a number the same way.
- **Feedback:** point each fader at its own address, `/jack/<motor>`. It follows what Jack is
  doing, so it drops to 0 when Jack rests the motor (the dead-man timeout, or a rest button). The
  mode toggle's address, `/jack/mouth/mode/show`, receives 1.0 or 0.0, so it shows the real
  mode. `/jack/<motor>/volts` and `/jack/<motor>/max_hold` can drive labels and LEDs.

### HTTP

JSON in and out. A bad request gets a one-line JSON error: `400` bad input, `404` unknown motor,
pose or path, `409` a mouth move while the mouth is in `live` mode, `413` a body over 4 KiB, `408` a body
that doesn't arrive within 5 s, `500` an unexpected error.

```bash
curl -X POST http://10.10.0.54:8080/hand -d '{"value": 0.5}'
curl -X POST http://10.10.0.54:8080/hand/pose -d '{"name": "curl", "seconds": 2}'
curl -X POST http://10.10.0.54:8080/hand/rest
curl -X POST http://10.10.0.54:8080/rest
curl -X POST http://10.10.0.54:8080/mouth/mode -d '{"mode": "show"}'
curl http://10.10.0.54:8080/status
```

`/status` reports the mouth mode, whether Mumble is connected, and per motor its command, volts,
whether max hold tripped, and its poses.

### Control page

Open `http://10.10.0.54:8080/` in a browser: a slider per motor, pose buttons, the live/show
switch and a status panel. A held slider resends its value, and releasing it lets the motor rest.
On macOS 15 and later the browser may need Local Network permission (System Settings > Privacy &
Security > Local Network) to reach the Pi.

### Mouth mode

`live` (the default) has the mouth follow the voice, and mouth commands are refused (HTTP `409`,
OSC logged and ignored). `show` has it follow commands like the other motors. Switch at runtime
with `/jack/mouth/mode show` (OSC) or `POST /mouth/mode`; the startup mode is `JACK_MOUTH_MODE`,
and a restart returns to it. A switch never jumps the mouth: show takes over from where lip sync
left it (closing it with the rest pulse if nothing is commanded), live starts lip sync closed, and
any mouth command is dropped.

### Settings in `/etc/jack/jack.env`

| Variable | Default | Meaning |
|---|---|---|
| `JACK_OSC_PORT` | `9000` | OSC UDP port |
| `JACK_OSC_REPLY_PORT` | sender's port | UDP port OSC replies and feedback go to |
| `JACK_HTTP_PORT` | `8080` | HTTP TCP port |
| `JACK_CONTROL_TIMEOUT_S` | `0.5` | Seconds without a new value before a motor rests |
| `JACK_MOUTH_MODE` | `live` | Mouth mode at startup: `live` or `show` |
| `JACK_ROC_SOURCE_PORT` | `10001` | ROC audio (RTP) UDP port |
| `JACK_ROC_REPAIR_PORT` | `10002` | ROC repair (FEC) UDP port |
| `JACK_ROC_CONTROL_PORT` | `10003` | ROC control (RTCP) UDP port |
| `JACK_ROC_TARGET_LATENCY_MS` | `100` | ROC's latency target; raise it if the sound breaks up |

### Motor settings: `poses.toml`

`poses.toml` in the repo holds each motor's volts, slew, measured holds (safe drive time per
voltage), rest behaviour and named poses. The elbow is marked an uncalibrated placeholder until
you measure it. To
override on the Pi, create `/etc/jack/poses.toml` with only the keys you change, then
`sudo systemctl restart jack`:

```toml
[elbow]
max_v = 3.0
[elbow.poses]
up = { volts = 3.0, seconds = 0.5 }
```

A mistake in either file stops the app with a one-line message in `journalctl -u jack`.

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
