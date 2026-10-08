"""Jack talks and takes show control.

Boss's voice from ROC or Mumble plays on the 3.5 mm jack; the mouth follows it (live) or show commands
(show); hand, pivot and elbow follow OSC/HTTP show commands. This is the composition root: it reads
the settings, builds every adapter, and hands them to the talk loop.
"""

import os
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import NoReturn

from smbus2 import SMBus

from jack.adapters.audio.alsa_sink import open_alsa_sink
from jack.adapters.audio.mumble_voice import MumbleVoice, connect_mumble
from jack.adapters.audio.roc_voice import RocVoice, roc_recv_command
from jack.adapters.hardware.motor_hats import build_motors
from jack.adapters.network.http_server import start_http_server
from jack.adapters.network.osc_server import OscEndpoint, start_feedback
from jack.adapters.system.deployment import (
    ALSA_DEVICE,
    ALSA_PERIODS,
    I2C_BUS,
    MUMBLE_HOST,
    MUMBLE_PORT,
    MUMBLE_USER,
    POSES_PATHS,
    PWM_FREQ_HZ,
)
from jack.adapters.system.systemd_notify import notify
from jack.application.config import AppConfig, ConfigError, RocConfig, describe_overrides, load_app_config
from jack.application.talk_loop import run_talk_loop
from jack.show.control.control_board import ControlBoard
from jack.show.control.osc_feedback import Subscribers
from jack.show.control.osc_protocol import OscContext, handle_osc
from jack.show.motion.motors import SUPPLY_VOLTS
from jack.support.rate_limited_log import RateLimitedLog

# One log line per kind of bad OSC message per minute.
OSC_LOG_INTERVAL_S = 60.0


def exit_on_sigterm(_signum: int, _frame: FrameType | None) -> NoReturn:
    """Turn SIGTERM into SystemExit so the playback loop's cleanup brakes the motors."""
    raise SystemExit(0)


def ping_watchdog() -> None:
    """Tell systemd's watchdog the app is still running its loop."""
    notify("WATCHDOG=1")


def watchdog_noting_mumble(mumble_client: threading.Thread, log: Callable[[str], None]) -> Callable[[], None]:
    """The talk loop's once-a-second callback: ping the watchdog, and log once if Mumble's client thread died.

    pymumble's thread ends for good when the server rejects the login. Jack keeps running on ROC; a
    restart retries Mumble.
    """
    noted = False

    def check() -> None:
        nonlocal noted
        if not noted and not mumble_client.is_alive():
            noted = True
            log("Mumble stopped; ROC still works")
        ping_watchdog()

    return check


def start_roc_voice(config: RocConfig, max_backlog_frames: int) -> RocVoice:
    """roc-recv running and feeding a RocVoice, or a one-line exit saying what to install."""
    command = roc_recv_command(config.source_port, config.repair_port, config.control_port, config.target_latency_ms)
    voice = RocVoice(command, max_backlog_frames, print)
    try:
        voice.start()
    except FileNotFoundError as error:
        raise SystemExit("roc-recv not found: sudo apt install roc-toolkit-tools") from error
    return voice


def startup_line(config: AppConfig, motor_names: list[str]) -> str:
    """The one journal line that says what Jack is listening on and driving."""
    show, roc = config.show_control, config.roc
    reply_target = show.reply_port or "sender's port"
    uncalibrated = [name for name, profile in config.profiles.items() if not profile.calibrated]
    return (
        f"Talking: ROC (UDP {roc.source_port}/{roc.repair_port}/{roc.control_port}, "
        f"{roc.target_latency_ms:g} ms) and Mumble voice on {ALSA_DEVICE}; "
        f"motors {', '.join(motor_names)} on {SUPPLY_VOLTS} V; "
        f"mouth {show.mouth_mode}; OSC UDP {show.osc_port}, HTTP {show.http_port}; "
        f"OSC replies to {reply_target}"
        + (f"; uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
    )


def main() -> None:
    signal.signal(signal.SIGTERM, exit_on_sigterm)
    try:
        config = load_app_config(os.environ, POSES_PATHS, SUPPLY_VOLTS)
    except ConfigError as error:
        raise SystemExit(str(error)) from error
    settings, profiles, show = config.settings, config.profiles, config.show_control
    if overrides := describe_overrides(settings):
        print(f"Mouth setting overrides: {overrides}")  # noqa: T201
    board = ControlBoard(profiles, show.timeout_s, show.mouth_mode, time.monotonic)
    voice = MumbleVoice(settings.max_backlog_frames, print)
    mumble_client = connect_mumble(voice, MUMBLE_HOST, MUMBLE_PORT, MUMBLE_USER, config.mumble_password)
    roc_voice = start_roc_voice(config.roc, settings.max_backlog_frames)
    try:
        with SMBus(I2C_BUS) as bus:
            try:
                motors = build_motors(bus, PWM_FREQ_HZ)
            except OSError as error:
                raise SystemExit(str(error)) from error
            sink = open_alsa_sink(ALSA_DEVICE, ALSA_PERIODS, print)
            endpoint = OscEndpoint("0.0.0.0", show.osc_port)
            subscribers = Subscribers(time.monotonic)
            osc_log = RateLimitedLog(print, OSC_LOG_INTERVAL_S, time.monotonic)
            context = OscContext(
                profiles=profiles, board=board, subscribers=subscribers, send=endpoint.send,
                mumble_connected=lambda: voice.connected, reply_port=show.reply_port, log=osc_log,
            )
            endpoint.serve(lambda address, args, sender: handle_osc(address, args, sender, context))
            start_feedback(endpoint, subscribers, lambda: board.status(voice.connected), osc_log)
            start_http_server("0.0.0.0", show.http_port, board, profiles, lambda: voice.connected)
            print(startup_line(config, list(motors)))  # noqa: T201
            notify("READY=1")
            run_talk_loop(
                [voice, roc_voice], sink, motors, profiles, settings, board, SUPPLY_VOLTS,
                watchdog_noting_mumble(mumble_client, print),
            )
    finally:
        roc_voice.close()


if __name__ == "__main__":
    main()
