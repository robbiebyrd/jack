import dataclasses
import signal

import pytest

import main
from jack.adapters.system.deployment import POSES_PATHS
from jack.application.config import RocConfig, load_app_config
from jack.show.motion.motors import SUPPLY_VOLTS
from tests.profiles import PROFILES


def test_sigterm_handler_raises_system_exit_so_cleanup_runs():
    with pytest.raises(SystemExit) as exc_info:
        main.exit_on_sigterm(signal.SIGTERM, None)
    assert exc_info.value.code == 0


def test_watchdog_ping_reaches_systemd(notify_socket):
    main.ping_watchdog()
    assert notify_socket.recv(64) == b"WATCHDOG=1"


class StandInClient:
    def __init__(self, alive: bool):
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


def test_the_watchdog_pings_while_the_mumble_client_lives(notify_socket, caplog):
    main.watchdog_noting_mumble(StandInClient(alive=True))()
    assert notify_socket.recv(64) == b"WATCHDOG=1"
    assert caplog.messages == []


def test_a_dead_mumble_client_is_logged_once_and_the_watchdog_keeps_pinging(notify_socket, caplog):
    check = main.watchdog_noting_mumble(StandInClient(alive=False))
    check()
    check()
    assert [notify_socket.recv(64), notify_socket.recv(64)] == [b"WATCHDOG=1", b"WATCHDOG=1"]
    assert caplog.messages == ["Mumble stopped; ROC still works"]


def test_a_missing_roc_recv_exits_saying_what_to_install(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty directory: no roc-recv to find
    with pytest.raises(SystemExit) as exit_info:
        main.start_roc_voice(RocConfig(10001, 10002, 10003, 100.0), 10)
    assert exit_info.value.code == "roc-recv not found: sudo apt install roc-toolkit-tools"


def test_the_startup_line_names_the_ports_mode_motors_and_uncalibrated_motors():
    config = load_app_config({"JACK_MUMBLE_PASSWORD": "x", "JACK_MOUTH_MODE": "show"}, POSES_PATHS, SUPPLY_VOLTS)
    line = main.startup_line(config, ["mouth", "hand", "pivot", "elbow"])
    assert "UDP 10001/10002/10003" in line and "100 ms" in line
    assert "mouth show" in line and "OSC UDP 9000" in line and "HTTP 8080" in line
    assert "OSC replies to sender's port" in line
    assert line.endswith("uncalibrated: elbow")  # poses.toml's elbow is a placeholder


def test_the_startup_line_omits_uncalibrated_when_every_motor_is_calibrated():
    config = load_app_config({"JACK_MUMBLE_PASSWORD": "x", "JACK_OSC_REPLY_PORT": "21601"}, POSES_PATHS, SUPPLY_VOLTS)
    calibrated = {name: dataclasses.replace(profile, calibrated=True) for name, profile in PROFILES.items()}
    line = main.startup_line(dataclasses.replace(config, profiles=calibrated), list(calibrated))
    assert "uncalibrated" not in line
    assert line.endswith("OSC replies to 21601")
