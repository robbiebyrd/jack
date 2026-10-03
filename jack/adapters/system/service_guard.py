"""Keeps hand-run tools off the HAT and the sound card while the jack service is using them."""

import subprocess
from pathlib import Path

APP_SERVICE = "jack.service"
STOP_APP_FIRST = f"{APP_SERVICE} is running and driving the HAT. Stop it first: sudo systemctl stop jack"
# Holds the Mumble password, so only root and the jack group can enter it (deploy/install.sh).
APP_CONFIG_DIR = Path("/etc/jack")


def app_is_running() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", APP_SERVICE], check=False).returncode == 0


def tool_error_message(error: Exception) -> str:
    """The error as text, plus how to run the tool as jack when the app's config directory was unreadable."""
    message = str(error)
    if isinstance(error, PermissionError) and error.filename and Path(error.filename).is_relative_to(APP_CONFIG_DIR):
        message += (
            f"\nOnly the jack user can read {APP_CONFIG_DIR}. "
            "Run the tool as jack: sudo -u jack /opt/jack-venv/bin/python ..."
        )
    return message
