#!/bin/bash
# One-time setup of the jack app and its auto-updater on the Raspberry Pi. Run as root.
# Safe to re-run: every step skips or overwrites idempotently.
set -euo pipefail

REPO_URL=https://github.com/robbiebyrd/jack.git
REPO_DIR=/opt/jack
BRANCH=main

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash install.sh" >&2
  exit 1
fi

apt-get update
apt-get install -y git python3-smbus2

# 0 = enable in raspi-config's non-interactive mode
raspi-config nonint do_i2c 0

if ! id jack &>/dev/null; then
  useradd --system --user-group --no-create-home --shell /usr/sbin/nologin --groups i2c jack
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi

install -m 0644 \
  "$REPO_DIR/deploy/jack.service" \
  "$REPO_DIR/deploy/jack-update.service" \
  "$REPO_DIR/deploy/jack-update.timer" \
  /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now jack.service jack-update.timer

echo "Installed. If /dev/i2c-1 is missing, reboot: sudo reboot"
