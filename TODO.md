# Still to do

Show control is live on the Pi (deployed 2026-10-02, commit 6810f2a). These steps remain.

## 1. Calibrate each new motor once it's wired

Mouth, hand and pivot were calibrated on 2026-10-02. The elbow doesn't respond yet (to check inside the animatronic) and still runs on placeholder values: 2 V poses, 0.5 s long, 1 s holds at ±2 V.

Once it moves:

1. `sudo systemctl stop jack`
2. `sudo -u jack /opt/jack-venv/bin/python /opt/jack/calibrate.py elbow`
3. Find:
   - which sign raises it
   - a working voltage
   - how long it can safely hold at each voltage you use (these become its `holds`)
4. Put the values in `/etc/jack/poses.toml`. Use the same layout as the repo's `poses.toml`, and include only the keys you change.
5. `sudo systemctl start jack`

Once the values are right, copy them into the repo's `poses.toml` and set `calibrated = true`.

## 2. Choose brake or coast at rest

The hand and pivot stay where they stop, so this applies only if the elbow turns out to spring back. If it comes back to rest sluggishly, set `rest = "coast"` for it in `/etc/jack/poses.toml`. The default is `"brake"`. Coasting hasn't been tried on the hardware yet.

## 3. Rerun the recovery checks with four motors

Run each of these and confirm Jack comes back on its own and every motor rests:

- `sudo kill -9 $(systemctl show -p MainPID --value jack)`: crash
- `sudo kill -STOP $(systemctl show -p MainPID --value jack)`: hang, so the watchdog restarts Jack
- `sudo reboot`
- `sudo systemctl restart mumble-server`

## 4. Check TouchOSC behaviour on the real layout

See the README's TouchOSC section for setup: host `10.10.0.54`, send port 9000, subscribe with your receive port (1024 or higher).

- **Hold a fader still for a second or more.** If the motor rests and the fader drops to 0 under your finger, TouchOSC isn't resending while held. Raise `JACK_CONTROL_TIMEOUT_S` in `/etc/jack/jack.env`, then `sudo systemctl restart jack`. Each motor's `holds` still cap how long any hold lasts.
- **Watch whether faders and toggles twitch or fight you** while Jack sends feedback. That would mean TouchOSC re-sends values it receives on a control's address, which interferes with the dead-man rule. If it happens, tell Claude.
- **Use a toggle button,** not a momentary one, on `/jack/mouth/mode/show`.

## Optional

- Record the second HAT's board-check meter readings in `SPEC.md` ("Hardware facts", second-HAT bullet).
