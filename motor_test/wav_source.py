"""VoiceSource that plays one WAV file, a frame per tick, for tuning the mouth without Mumble."""

import wave
from pathlib import Path

from motor_test.pcm import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_BYTES, SAMPLE_RATE_HZ


class WavSource:
    def __init__(self, path: str | Path):
        self._wav = wave.open(str(path), "rb")
        found = (self._wav.getnchannels(), self._wav.getsampwidth(), self._wav.getframerate())
        if found != (1, SAMPLE_BYTES, SAMPLE_RATE_HZ):
            self._wav.close()
            channels, width, rate = found
            raise ValueError(
                f"{path} is {channels}-channel {8 * width}-bit {rate} Hz; it must be mono 16-bit "
                f"{SAMPLE_RATE_HZ} Hz. On a Mac: afconvert -f WAVE -d LEI16@{SAMPLE_RATE_HZ} -c 1 IN OUT.wav"
            )
        self.finished = False

    def take_frames(self) -> list[bytes]:
        """The file's next frame (the last one padded with silence), then nothing once it has ended."""
        if self.finished:
            return []
        data = self._wav.readframes(FRAME_SAMPLES)
        if not data:
            self.finished = True
            self._wav.close()
            return []
        return [data.ljust(FRAME_BYTES, b"\0")]
