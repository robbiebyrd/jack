"""VoiceSource for a Roc Toolkit stream: Jack runs roc-recv and hears what a sender (roc-vad) plays.

roc-recv writes raw stereo signed 16-bit samples at 48 kHz to stdout in real time, both channels
identical, and digital silence while nobody sends. A reader thread keeps the left channel, cuts
it into talk-loop frames, and restarts roc-recv if it exits. See "ROC voice" in SPEC.md.
"""

import subprocess
import threading
import time
from array import array
from collections.abc import Callable, Sequence

from jack.show.audio.frame_queue import FrameQueue
from jack.show.audio.pcm import SAMPLE_RATE_HZ
from jack.support.rate_limited_log import RateLimitedLog

# One stereo sample pair: two signed 16-bit samples.
STEREO_SAMPLE_BYTES = 4
READ_BYTES = 65536
RESTART_LOG_INTERVAL_S = 60.0


def roc_recv_command(source_port: int, repair_port: int, control_port: int, target_latency_ms: float) -> list[str]:
    """roc-recv listening on every interface, writing raw stereo s16 at the talk loop's rate to stdout."""
    return [
        "roc-recv",
        "-s", f"rtp+rs8m://0.0.0.0:{source_port}",
        "-r", f"rs8m://0.0.0.0:{repair_port}",
        "-c", f"rtcp://0.0.0.0:{control_port}",
        "-o", "file:-",
        "--output-format", "s16",
        "--rate", str(SAMPLE_RATE_HZ),
        f"--target-latency={target_latency_ms:g}ms",
    ]


class RocVoice:
    def __init__(
        self,
        command: Sequence[str],
        max_backlog_frames: int,
        log: Callable[[str], None],
        restart_delay_s: float = 2.0,
        stop_grace_s: float = 1.0,
    ):
        self._command = list(command)
        self._queue = FrameQueue(max_backlog_frames)
        self._log = RateLimitedLog(log, RESTART_LOG_INTERVAL_S, time.monotonic)
        self._restart_delay_s = restart_delay_s
        self._stop_grace_s = stop_grace_s
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start roc-recv and its reader thread. Raises FileNotFoundError when roc-recv isn't installed."""
        self._process = self._spawn()
        self._thread = threading.Thread(target=self._run, name="roc-voice", daemon=True)
        self._thread.start()

    def take_frames(self) -> list[bytes]:
        """The next frame of sound, skipping all queued digital silence so it mixes as no voice.

        roc-recv writes silence continuously, so the queue never drains on its own; dropping every
        queued silent frame at once keeps a backlog from delaying the next thing said.
        """
        while (frame := self._queue.take()) is not None:
            if any(frame):
                return [frame]
        return []

    def close(self) -> None:
        """Stop roc-recv (SIGTERM, then a kill after the grace period) and the reader thread."""
        self._closed.set()
        with self._lock:
            process = self._process
        if process is not None:
            _stop(process, self._stop_grace_s)
        if self._thread is not None:
            self._thread.join()

    def _spawn(self) -> subprocess.Popen:
        return subprocess.Popen(self._command, stdout=subprocess.PIPE)

    def _run(self) -> None:
        process = self._process
        while True:
            if process is not None:
                code = self._read_until_exit(process)
                if self._closed.is_set():
                    return
                self._log("exited", f"roc-recv exited (code {code}); restarting")
            if self._closed.wait(self._restart_delay_s):
                return
            try:
                process = self._spawn()
            except OSError as error:
                process = None
                self._log("spawn", f"roc-recv could not start ({error}); retrying")
                continue
            with self._lock:
                if self._closed.is_set():
                    # close() ran while this one was starting; nothing will read its pipe.
                    _stop(process, self._stop_grace_s)
                    process.stdout.close()
                    return
                self._process = process

    def _read_until_exit(self, process: subprocess.Popen) -> int:
        """Feed roc-recv's output to the queue until it ends; return its exit code."""
        pending = b""
        with process.stdout:
            while chunk := process.stdout.read1(READ_BYTES):
                data = pending + chunk
                whole = len(data) - len(data) % STEREO_SAMPLE_BYTES
                pending = data[whole:]
                left = array("h", data[:whole])[0::2].tobytes()
                self._queue.put(left)
        return process.wait()


def _stop(process: subprocess.Popen, grace_s: float) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(grace_s)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
