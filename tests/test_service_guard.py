import os

import pytest

from jack.adapters.system.service_guard import (
    APP_CONFIG_DIR,
    APP_SERVICE,
    STOP_APP_FIRST,
    app_is_running,
    tool_error_message,
)


@pytest.fixture
def fake_systemctl(tmp_path, monkeypatch):
    """Put a `systemctl` on PATH that records its arguments and exits with the chosen status."""

    def install(exit_code):
        args_log = tmp_path / "systemctl-args"
        script = tmp_path / "systemctl"
        script.write_text(f'#!/bin/sh\necho "$@" > "{args_log}"\nexit {exit_code}\n')
        script.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
        return args_log

    return install


def test_running_app_is_detected(fake_systemctl):
    args_log = fake_systemctl(0)
    assert app_is_running()
    assert args_log.read_text().strip() == "is-active --quiet jack.service"


def test_stopped_app_is_detected(fake_systemctl):
    fake_systemctl(3)
    assert not app_is_running()


def test_message_says_how_to_stop_the_app():
    assert APP_SERVICE in STOP_APP_FIRST
    assert "sudo systemctl stop jack" in STOP_APP_FIRST


def test_unreadable_app_config_says_to_run_the_tool_as_jack():
    error = PermissionError(13, "Permission denied", str(APP_CONFIG_DIR / "poses.toml"))
    message = tool_error_message(error)
    assert message.startswith(str(error))
    assert "sudo -u jack /opt/jack-venv/bin/python" in message


def test_other_unreadable_files_get_no_hint():
    error = PermissionError(13, "Permission denied", "/home/someone/voice.wav")
    assert tool_error_message(error) == str(error)


def test_other_errors_are_reported_as_they_are():
    assert tool_error_message(ValueError("max_v = 99 is too high")) == "max_v = 99 is too high"
