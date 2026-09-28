"""AudioSink on an ALSA playback device (the Pi's 3.5 mm jack) through python3-alsaaudio."""

from collections.abc import Callable

from motor_test.pcm import FRAME_SAMPLES, SAMPLE_RATE_HZ

# snd_strerror(-EPIPE): how alsaaudio reports that the device ran dry (an underrun).
UNDERRUN_MESSAGE = "Broken pipe"
# Log the first underrun and then every this-many, so a struggling loop can't flood the journal.
UNDERRUN_LOG_EVERY = 100


class AlsaSink:
    """Plays frames on an open alsaaudio PCM; underruns are logged and ridden through."""

    def __init__(self, pcm, error_type: type[Exception], log: Callable[[str], None]):
        self._pcm = pcm
        self._error_type = error_type
        self._log = log
        self.underruns = 0

    def write(self, frame: bytes) -> None:
        try:
            self._pcm.write(frame)
        except self._error_type as error:
            if not str(error).startswith(UNDERRUN_MESSAGE):
                raise
            self.underruns += 1
            if self.underruns == 1 or self.underruns % UNDERRUN_LOG_EVERY == 0:
                self._log(f"Audio underrun #{self.underruns}; carrying on")

    def close(self) -> None:
        self._pcm.close()


def open_alsa_sink(device: str, periods: int, log: Callable[[str], None]) -> AlsaSink:
    """Open `device` for blocking mono 16-bit playback, one talk-loop frame per ALSA period."""
    import alsaaudio  # Only on the Pi (python3-alsaaudio); tests use AlsaSink with a stand-in PCM.

    pcm = alsaaudio.PCM(
        type=alsaaudio.PCM_PLAYBACK,
        mode=alsaaudio.PCM_NORMAL,
        rate=SAMPLE_RATE_HZ,
        channels=1,
        format=alsaaudio.PCM_FORMAT_S16_LE,
        periodsize=FRAME_SAMPLES,
        periods=periods,
        device=device,
    )
    return AlsaSink(pcm, alsaaudio.ALSAAudioError, log)
