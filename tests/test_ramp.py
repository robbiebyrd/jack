import pytest

from jack.show.motion.ramp import PWM_MAX_COUNT, segment_profile, volts_to_count, whole_steps


def test_segment_profile_holds_each_level_for_its_duration():
    counts = segment_profile([(-6.0, -6.0, 0.5), (-3.0, -3.0, 1.0), (0.0, 0.0, 2.0)], supply_volts=12.0, step_s=0.05)
    assert counts == [-2048] * 10 + [-1024] * 20 + [0] * 40


def test_segment_profile_ramps_linearly_reaching_the_end_level_on_the_last_step():
    counts = segment_profile([(0.0, -6.0, 0.25)], supply_volts=12.0, step_s=0.05)
    assert counts == [-410, -819, -1229, -1638, -2048]  # -1.2, -2.4, -3.6, -4.8, -6.0 V


@pytest.mark.parametrize(
    "segments, supply_volts, step_s",
    [
        ([], 12.0, 0.05),  # nothing to play
        ([(-13.0, -13.0, 0.5)], 12.0, 0.05),  # below -supply
        ([(0.0, -13.0, 0.5)], 12.0, 0.05),  # ramp ends below -supply
        ([(13.0, 0.0, 0.5)], 12.0, 0.05),  # ramp starts above supply
        ([(-6.0, -6.0, 0.5)], 0.0, 0.05),  # zero supply
        ([(-6.0, -6.0, 0.03)], 12.0, 0.05),  # duration not a whole number of steps
        ([(-6.0, -6.0, 0.0)], 12.0, 0.05),  # zero-length segment
        ([(-6.0, -6.0, 0.5)], 12.0, 0.0),  # zero step
    ],
)
def test_invalid_segments_are_rejected(segments, supply_volts, step_s):
    with pytest.raises(ValueError):
        segment_profile(segments, supply_volts, step_s)


def test_whole_steps_counts_steps_in_a_duration():
    assert whole_steps(0.16, 0.02) == 8
    assert whole_steps(0.25, 0.05) == 5


@pytest.mark.parametrize("seconds", [0.15, 0.0, -0.02])
def test_whole_steps_rejects_partial_or_empty_durations(seconds):
    with pytest.raises(ValueError):
        whole_steps(seconds, 0.02)


def test_volts_to_count_is_signed_and_capped_below_the_full_off_bit():
    assert volts_to_count(6.0, 12.0) == 2048
    assert volts_to_count(-6.0, 12.0) == -2048
    assert volts_to_count(12.0, 12.0) == PWM_MAX_COUNT
    assert volts_to_count(0.0, 12.0) == 0
