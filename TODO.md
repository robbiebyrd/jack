# Still to do

Show control is live on the Pi (deployed 2026-10-02, commit 6810f2a). These steps remain.

## 1. Calibrate each new motor once it's wired

Hand, pivot and elbow run on placeholder values until measured: 2 V poses, 0.5 s long, 1 s max hold.

For each motor:

1. `sudo systemctl stop jack`
2. `/opt/jack-venv/bin/python /opt/jack/calibrate.py hand` (or `pivot`, or `elbow`)
3. Find:
   - which sign moves it the right way (curl the hand, swing the pivot right, raise the elbow)
   - a working voltage
   - how long it can safely hold
4. Put the values in `/etc/jack/poses.toml`. Use the same layout as the repo's `poses.toml`, and include only the keys you change.
5. `sudo systemctl start jack`

Once the values are right, copy them into the repo's `poses.toml` and set `calibrated = true` for that motor.

## 2. Choose brake or coast at rest

If a spring-return motor comes back to rest sluggishly, set `rest = "coast"` for it in `/etc/jack/poses.toml`. The default is `"brake"`. Coasting hasn't been tried on the hardware yet.

## 3. Rerun the recovery checks with four motors

Run each of these and confirm Jack comes back on its own and every motor rests:

- `sudo kill -9 $(systemctl show -p MainPID --value jack)`: crash
- `sudo kill -STOP $(systemctl show -p MainPID --value jack)`: hang, so the watchdog restarts Jack
- `sudo reboot`
- `sudo systemctl restart mumble-server`

## Optional

- Record the second HAT's board-check meter readings in `SPEC.md` ("Hardware facts", second-HAT bullet).
