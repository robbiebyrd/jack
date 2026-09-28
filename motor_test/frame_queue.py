"""A bounded, thread-safe queue that cuts PCM chunks of any size into talk-loop frames.

A voice's network thread puts chunks in; the talk loop takes one frame per tick. When the
network delivers faster than the loop plays, the oldest audio is dropped so delay stays bounded.
"""

import threading
from collections import deque

from motor_test.pcm import FRAME_BYTES


class FrameQueue:
    def __init__(self, max_frames: int):
        if max_frames < 1:
            raise ValueError(f"max_frames must be at least 1, got {max_frames}")
        self._max_frames = max_frames
        self._frames: deque[bytes] = deque()
        self._partial = b""
        self._lock = threading.Lock()

    def put(self, pcm: bytes) -> int:
        """Append `pcm` as whole frames, keeping any remainder for the next chunk.

        Returns how many of the oldest frames were dropped to stay within max_frames.
        """
        with self._lock:
            data = self._partial + pcm
            whole = len(data) - len(data) % FRAME_BYTES
            for start in range(0, whole, FRAME_BYTES):
                self._frames.append(data[start : start + FRAME_BYTES])
            self._partial = data[whole:]
            dropped = max(0, len(self._frames) - self._max_frames)
            for _ in range(dropped):
                self._frames.popleft()
            return dropped

    def take(self) -> bytes | None:
        """The oldest whole frame, or None when there isn't one."""
        with self._lock:
            return self._frames.popleft() if self._frames else None
