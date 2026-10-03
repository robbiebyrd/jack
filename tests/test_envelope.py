import math

import pytest

from jack.show.audio.envelope import FLOOR_DB, EnvelopeFollower, rms_dbfs
from jack.show.audio.pcm import silence
from tests.audio import constant_frame, sine_frame


def test_digital_silence_reads_as_the_floor():
    assert FLOOR_DB == -90.0
    assert rms_dbfs(silence()) == FLOOR_DB


def test_full_scale_square_reads_0_dbfs():
    assert rms_dbfs(constant_frame(32767)) == pytest.approx(0.0)


def test_full_scale_sine_reads_about_minus_3_dbfs():
    assert rms_dbfs(sine_frame(32767)) == pytest.approx(-3.0103, abs=0.01)


def test_half_amplitude_is_about_6_db_quieter():
    assert rms_dbfs(sine_frame(16384)) - rms_dbfs(sine_frame(32767)) == pytest.approx(-6.02, abs=0.01)


def test_sound_quieter_than_the_floor_reads_as_the_floor():
    assert rms_dbfs(constant_frame(1)) == FLOOR_DB


def test_follower_starts_at_the_floor():
    assert EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02).level_db == FLOOR_DB


def test_rising_level_follows_the_attack_time_constant():
    follower = EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02)
    assert follower.update(0.0) == pytest.approx(FLOOR_DB * math.exp(-0.02 / 0.01))


def test_falling_level_follows_the_release_time_constant():
    follower = EnvelopeFollower(attack_s=0.01, release_s=0.08, tick_s=0.02)
    for _ in range(50):
        follower.update(0.0)
    settled = follower.level_db
    expected = FLOOR_DB + (settled - FLOOR_DB) * math.exp(-0.02 / 0.08)
    assert follower.update(FLOOR_DB) == pytest.approx(expected)


@pytest.mark.parametrize("attack_s, release_s, tick_s", [(0, 0.08, 0.02), (0.01, -1, 0.02), (0.01, 0.08, 0)])
def test_time_constants_and_tick_must_be_positive(attack_s, release_s, tick_s):
    with pytest.raises(ValueError):
        EnvelopeFollower(attack_s, release_s, tick_s)
