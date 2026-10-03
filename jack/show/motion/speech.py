"""Random mouth movements that imitate speaking, built from the calibrated poses.

Each phrase is a few words of one to three syllables. A syllable opens the mouth
part way with a short -2 V pulse and closes it with +0.5 V, the gentle close that
works from relaxed open. About one syllable in eight is emphasised: a ramped full
open, then the +1 V close that works from any pose. Every phrase ends closed,
followed by a pause.
"""

import random

from jack.show.motion.mouth import STEP_S, Segment, hold, open_fully, rest

# A phrase is one playback cycle, and the watchdog is pinged once per cycle.
MAX_PHRASE_S = 4.5

WORDS_PER_PHRASE = (1, 4)
SYLLABLES_PER_WORD = (1, 3)

SYLLABLE_OPEN_VOLTS = -2.0
SYLLABLE_OPEN_S = (0.15, 0.4)
SYLLABLE_CLOSE: Segment = hold(0.5, 0.15)

EMPHASIS_CHANCE = 1 / 8
EMPHASIS_HOLD_S = (0.15, 0.3)
EMPHASIS_CLOSE: Segment = hold(1.0, 0.25)

SYLLABLE_GAP_S = (0.05, 0.15)
WORD_GAP_S = (0.1, 0.3)
PHRASE_PAUSE_S = (0.5, 1.5)


def random_phrase(rng: random.Random) -> tuple[Segment, ...]:
    """Return one phrase of talking movements, at most MAX_PHRASE_S long."""
    # Leave room for the longest closing pause so the phrase never overruns.
    budget_s = MAX_PHRASE_S - PHRASE_PAUSE_S[1]
    segments: list[Segment] = []
    for word in range(rng.randint(*WORDS_PER_PHRASE)):
        for syllable in range(rng.randint(*SYLLABLES_PER_WORD)):
            gap = () if not segments else rest(_pick(rng, WORD_GAP_S if syllable == 0 else SYLLABLE_GAP_S))
            spoken = gap + _syllable(rng)
            if segments and _duration(segments) + _duration(spoken) > budget_s:
                return tuple(segments) + rest(_pick(rng, PHRASE_PAUSE_S))
            segments += spoken
    return tuple(segments) + rest(_pick(rng, PHRASE_PAUSE_S))


def _syllable(rng: random.Random) -> tuple[Segment, ...]:
    if rng.random() < EMPHASIS_CHANCE:
        return open_fully(_pick(rng, EMPHASIS_HOLD_S)) + (EMPHASIS_CLOSE,)
    return (hold(SYLLABLE_OPEN_VOLTS, _pick(rng, SYLLABLE_OPEN_S)), SYLLABLE_CLOSE)


def _pick(rng: random.Random, bounds: tuple[float, float]) -> float:
    """A random duration within `bounds`, in whole playback steps."""
    low, high = (round(limit / STEP_S) for limit in bounds)
    return round(rng.randint(low, high) * STEP_S, 10)


def _duration(segments) -> float:
    return sum(seconds for _, _, seconds in segments)
