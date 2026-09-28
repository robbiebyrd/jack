import math
import random

import pytest

from motor_test.mouth import STEP_S
from motor_test.speech import (
    EMPHASIS_CHANCE,
    MAX_PHRASE_S,
    PHRASE_PAUSE_S,
    SYLLABLE_CLOSE,
    random_phrase,
)

SEEDS = range(200)


def phrase(seed):
    return random_phrase(random.Random(seed))


def duration(segments):
    return sum(seconds for _, _, seconds in segments)


@pytest.mark.parametrize("seed", SEEDS)
def test_phrase_fits_inside_the_watchdog_budget(seed):
    assert 0 < duration(phrase(seed)) <= MAX_PHRASE_S + 1e-9


@pytest.mark.parametrize("seed", SEEDS)
def test_every_segment_is_a_whole_number_of_steps(seed):
    for _, _, seconds in phrase(seed):
        steps = seconds / STEP_S
        assert steps >= 1 and math.isclose(steps, round(steps))


@pytest.mark.parametrize("seed", SEEDS)
def test_voltages_stay_within_the_calibrated_range(seed):
    for start, end, _ in phrase(seed):
        assert -6.0 <= start <= 1.0 and -6.0 <= end <= 1.0


@pytest.mark.parametrize("seed", SEEDS)
def test_phrase_ends_with_a_close_then_a_pause(seed):
    segments = phrase(seed)
    pause_volts, _, pause_s = segments[-1]
    close_volts, _, _ = segments[-2]
    assert pause_volts == 0.0 and PHRASE_PAUSE_S[0] <= pause_s <= PHRASE_PAUSE_S[1]
    assert close_volts > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_every_opening_run_is_ended_by_a_close(seed):
    segments = phrase(seed)
    for current, following in zip(segments, segments[1:]):
        opening_now = min(current[:2]) < 0
        opening_next = min(following[:2]) < 0
        if opening_now and not opening_next:
            assert following[0] > 0


def test_same_seed_gives_the_same_phrase():
    assert phrase(7) == phrase(7)


def test_different_seeds_give_different_phrases():
    assert len({phrase(seed) for seed in SEEDS}) > 150


def test_normal_syllables_close_gently_from_relaxed_open():
    assert SYLLABLE_CLOSE == (0.5, 0.5, 0.15)


def test_emphasis_is_occasional():
    phrases = [phrase(seed) for seed in SEEDS]
    syllables = sum(1 for p in phrases for start, _, _ in p if start > 0)
    emphasised = sum(1 for p in phrases for start, end, _ in p if min(start, end) <= -6.0 and start == end)
    assert 0 < emphasised / syllables < 3 * EMPHASIS_CHANCE
