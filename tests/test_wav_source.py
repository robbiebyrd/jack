import pytest

from motor_test.pcm import FRAME_BYTES
from motor_test.wav_source import WavSource
from tests.audio import constant_frame, write_wav


def test_plays_one_frame_per_tick_padding_the_last_then_finishes(tmp_path):
    tail = constant_frame(3)[: FRAME_BYTES // 2]
    source = WavSource(write_wav(tmp_path / "voice.wav", constant_frame(1) + constant_frame(2) + tail))
    assert source.take_frames() == [constant_frame(1)]
    assert source.take_frames() == [constant_frame(2)]
    assert source.take_frames() == [tail + bytes(FRAME_BYTES // 2)]
    assert not source.finished
    assert source.take_frames() == []
    assert source.finished
    assert source.take_frames() == []


@pytest.mark.parametrize(
    "channels, sample_width, rate",
    [(2, 2, 48000), (1, 2, 44100), (1, 1, 48000)],
)
def test_wrong_format_is_rejected_with_how_to_convert(tmp_path, channels, sample_width, rate):
    path = write_wav(tmp_path / "voice.wav", bytes(channels * sample_width * 10), channels, sample_width, rate)
    with pytest.raises(ValueError, match="mono 16-bit 48000 Hz") as error:
        WavSource(path)
    assert "afconvert" in str(error.value)


def test_a_file_that_is_not_a_wav_is_rejected_as_unreadable(tmp_path):
    path = tmp_path / "voice.wav"
    path.write_bytes(b"not a wav")
    with pytest.raises(ValueError, match="not a readable WAV"):
        WavSource(path)
