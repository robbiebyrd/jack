"""Keeps hand-run tools off the HAT and the sound card while the jack service is using them."""

import subprocess

APP_SERVICE = "jack.service"
STOP_APP_FIRST = f"{APP_SERVICE} is running and driving the HAT. Stop it first: sudo systemctl stop jack"


def app_is_running() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", APP_SERVICE], check=False).returncode == 0
