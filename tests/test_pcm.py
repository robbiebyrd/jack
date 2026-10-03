import pytest

from jack.show.audio.pcm import FRAME_BYTES, FRAME_SAMPLES, SAMPLE_RATE_HZ, TICK_S, TICKS_PER_SECOND, mix, silence
from tests.audio import constant_frame


def test_a_frame_is_20_ms_of_48_khz_mono_16_bit_audio():
    assert SAMPLE_RATE_HZ == 48000
    assert TICK_S == 0.02
    assert FRAME_SAMPLES == 960
    assert FRAME_BYTES == 1920
    assert TICKS_PER_SECOND == 50


def test_silence_is_one_frame_of_zeros():
    assert silence() == bytes(FRAME_BYTES)


def test_mixing_one_frame_returns_it_unchanged():
    frame = constant_frame(1234)
    assert mix([frame]) == frame


def test_mixing_sums_samples():
    assert mix([constant_frame(1000), constant_frame(-300)]) == constant_frame(700)


def test_mixing_clips_to_the_16_bit_range():
    assert mix([constant_frame(30000), constant_frame(30000)]) == constant_frame(32767)
    assert mix([constant_frame(-30000), constant_frame(-30000)]) == constant_frame(-32768)


def test_mixing_rejects_no_frames():
    with pytest.raises(ValueError):
        mix([])


@pytest.mark.parametrize("frames", [[bytes(10)], [constant_frame(0), bytes(10)]])
def test_mixing_rejects_a_frame_of_the_wrong_size(frames):
    with pytest.raises(ValueError):
        mix(frames)
