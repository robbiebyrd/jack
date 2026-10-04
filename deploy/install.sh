#!/bin/bash
# One-time setup of the jack app and its auto-updater on the Raspberry Pi. Run as root.
# Safe to re-run: every step skips or overwrites idempotently.
set -euo pipefail

REPO_URL=https://github.com/robbiebyrd/jack.git
REPO_DIR=/opt/jack
BRANCH=main
VENV_DIR=/opt/jack-venv
ENV_FILE=/etc/jack/jack.env

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3-smbus2 raspi-config mumble-server python3-alsaaudio python3-opuslib python3-protobuf python3-venv roc-toolkit-tools

# 0 = enable in raspi-config's non-interactive mode
raspi-config nonint do_i2c 0

# Keep the journal across reboots, written to the SD card within a second, so a crash or hang
# leaves the logs from just before it.
install -d -m 0755 /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSyncIntervalSec=1s\n' > /etc/systemd/journald.conf.d/persistent.conf
systemctl restart systemd-journald

# Keep the kernel's crash log across a reset (moved to /var/lib/systemd/pstore/ on the next boot; takes
# effect after a reboot), and reboot 10 s after a kernel panic instead of staying frozen.
grep -qxF 'dtoverlay=ramoops-pi4' /boot/firmware/config.txt || echo 'dtoverlay=ramoops-pi4' >> /boot/firmware/config.txt
echo 'kernel.panic = 10' > /etc/sysctl.d/90-jack-panic.conf
echo 10 > /proc/sys/kernel/panic

if ! id jack &>/dev/null; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin --groups i2c,audio jack
fi
# Existing installs predate the audio group.
usermod --append --groups i2c,audio jack

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

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

install -m 0644 \
  "$REPO_DIR/deploy/jack.service" \
  "$REPO_DIR/deploy/jack-update.service" \
  "$REPO_DIR/deploy/jack-update.timer" \
  "$REPO_DIR/deploy/jack-health.service" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable jack.service jack-update.timer
systemctl start jack-update.timer
# Debug log for the Pi's hangs (SPEC.md "Hardware facts").
systemctl enable --now jack-health.service
# Restart (or first start) the app so a changed unit takes effect; on a fresh install it can't start until the password is set.
systemctl restart jack.service || echo "jack.service did not start yet (see: journalctl -u jack); set the password below, then restart it"

echo "Installed. If /dev/i2c-1 is missing, reboot: sudo reboot"
echo "To talk: set serverpassword= in /etc/mumble/mumble-server.ini and JACK_MUMBLE_PASSWORD= in $ENV_FILE"
echo "to the same password, then: sudo systemctl restart mumble-server jack"
