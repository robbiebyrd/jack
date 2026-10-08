"""VoiceSource for a Mumble channel: Jack joins as a bot and hears everyone talking there.

pymumble delivers each talker's decoded audio on its own thread; every talker gets a
FrameQueue so the talk loop can take one frame per talker per tick and mix them.
"""

import logging
import threading
from collections.abc import Mapping
from typing import Protocol

from jack.show.audio.frame_queue import FrameQueue
from jack.show.audio.pcm import TICK_S

log = logging.getLogger(__name__)


class SoundChunk(Protocol):
    """The part of pymumble's SoundChunk the voice reads."""

    pcm: bytes


class MumbleVoice:
    def __init__(self, max_backlog_frames: int):
        self._max_backlog_frames = max_backlog_frames
        self._queues: dict[int, FrameQueue] = {}
        self._lock = threading.Lock()
        self.connected = False

    def on_sound(self, user: Mapping[str, object], chunk: SoundChunk) -> None:
        """pymumble's sound-received callback; runs on pymumble's thread."""
        with self._lock:
            queue = self._queues.setdefault(user["session"], FrameQueue(self._max_backlog_frames))
        dropped = queue.put(chunk.pcm)
        if dropped:
            log.warning("Dropped %d ms of %s's voice to keep up", round(dropped * TICK_S * 1000), user["name"])

    def on_connected(self) -> None:
        self.connected = True
        log.info("Connected to Mumble")

    def on_disconnected(self) -> None:
        self.connected = False
        log.warning("Disconnected from Mumble; pymumble retries every 10 s")

    def take_frames(self) -> list[bytes]:
        with self._lock:
            queues = list(self._queues.values())
        return [frame for frame in (queue.take() for queue in queues) if frame is not None]


def connect_mumble(voice: MumbleVoice, host: str, port: int, user: str, password: str) -> threading.Thread:
    """Start a pymumble bot that feeds `voice` and reconnects on its own; returns the client (its thread)."""
    import pymumble_py3 as pymumble  # Only in the Pi's venv; tests drive MumbleVoice directly.
    from pymumble_py3.constants import (
        PYMUMBLE_CLBK_CONNECTED,
        PYMUMBLE_CLBK_DISCONNECTED,
        PYMUMBLE_CLBK_SOUNDRECEIVED,
    )

    client = pymumble.Mumble(host, user, port=port, password=password, reconnect=True, client_type=1)
    # pymumble's thread is not a daemon; without this the process could not exit on SIGTERM or a crash.
    client.daemon = True
    client.callbacks.set_callback(PYMUMBLE_CLBK_SOUNDRECEIVED, voice.on_sound)
    client.callbacks.set_callback(PYMUMBLE_CLBK_CONNECTED, voice.on_connected)
    client.callbacks.set_callback(PYMUMBLE_CLBK_DISCONNECTED, voice.on_disconnected)
    client.set_receive_sound(True)
    client.start()
    return client
