import pytest

from jack.adapters.audio.alsa_sink import AlsaSink
from tests.audio import constant_frame


class FakeAlsaError(Exception):
    pass


class ScriptedPcm:
    """Stand-in for alsaaudio.PCM: write number n raises errors[n-1] when that entry isn't None."""

    def __init__(self, errors=()):
        self.written = []
        self.closed = False
        self._errors = list(errors)

    def write(self, data):
        error = self._errors.pop(0) if self._errors else None
        if error is not None:
            raise error
        self.written.append(data)
        return len(data) // 2

    def close(self):
        self.closed = True


def make_sink(pcm):
    return AlsaSink(pcm, FakeAlsaError)


def test_frames_go_to_the_device(caplog):
    pcm = ScriptedPcm()
    make_sink(pcm).write(constant_frame(5))
    assert pcm.written == [constant_frame(5)]
    assert caplog.messages == []


def test_an_underrun_is_counted_logged_and_ridden_through(caplog):
    pcm = ScriptedPcm([FakeAlsaError("Broken pipe [Headphones]"), None])
    sink = make_sink(pcm)
    sink.write(constant_frame(1))
    sink.write(constant_frame(2))
    assert sink.underruns == 1
    assert caplog.messages == ["Audio underrun #1; carrying on"]
    assert pcm.written == [constant_frame(2)]


def test_repeated_underruns_log_the_first_and_every_hundredth(caplog):
    pcm = ScriptedPcm([FakeAlsaError("Broken pipe [Headphones]")] * 200)
    sink = make_sink(pcm)
    for _ in range(200):
        sink.write(constant_frame(0))
    assert caplog.messages == [f"Audio underrun #{n}; carrying on" for n in (1, 100, 200)]


def test_other_device_errors_propagate():
    sink = make_sink(ScriptedPcm([FakeAlsaError("No such device [Headphones]")]))
    with pytest.raises(FakeAlsaError):
        sink.write(constant_frame(0))


def test_close_closes_the_device():
    pcm = ScriptedPcm()
    make_sink(pcm).close()
    assert pcm.closed
