import pytest

from motor_test.ramp import PWM_MAX_COUNT, constant_profile, ramp_profile, segment_profile, square_profile, whole_steps


def test_six_volts_on_twelve_volt_supply_peaks_at_half_duty():
    assert max(ramp_profile(peak_volts=6.0, supply_volts=12.0, steps=50)) == 2048


def test_profile_has_requested_step_count():
    assert len(ramp_profile(6.0, 12.0, 50)) == 50


def test_profile_starts_at_zero_and_peaks_mid_cycle():
    counts = ramp_profile(6.0, 12.0, 50)
    assert counts[0] == 0
    assert counts[25] == 2048


def test_profile_rises_then_falls_symmetrically():
    counts = ramp_profile(6.0, 12.0, 50)
    assert counts[:26] == sorted(counts[:26])
    for k in range(1, 50):
        assert counts[k] == counts[50 - k]


def test_full_supply_peak_is_capped_below_the_full_off_bit():
    assert max(ramp_profile(12.0, 12.0, 50)) == PWM_MAX_COUNT


def test_zero_peak_produces_all_zero_counts():
    assert ramp_profile(0.0, 12.0, 50) == [0] * 50


@pytest.mark.parametrize(
    "peak_volts, supply_volts, steps",
    [
        (13.0, 12.0, 50),  # peak above supply
        (-1.0, 12.0, 50),  # negative peak
        (6.0, 0.0, 50),  # zero supply
        (6.0, -12.0, 50),  # negative supply
        (6.0, 12.0, 0),  # no steps
        (6.0, 12.0, 49),  # odd steps have no single peak sample
    ],
)
def test_invalid_inputs_are_rejected(peak_volts, supply_volts, steps):
    with pytest.raises(ValueError):
        ramp_profile(peak_volts, supply_volts, steps)


def test_square_holds_high_for_first_half_then_low_for_second_half():
    counts = square_profile(high_volts=3.0, low_volts=-3.0, supply_volts=12.0, steps=50)
    assert counts == [1024] * 25 + [-1024] * 25


def test_square_caps_full_supply_at_the_highest_usable_count():
    assert square_profile(12.0, -12.0, 12.0, 50) == [PWM_MAX_COUNT] * 25 + [-PWM_MAX_COUNT] * 25


@pytest.mark.parametrize(
    "high_volts, low_volts, supply_volts, steps",
    [
        (13.0, -3.0, 12.0, 50),  # high above supply
        (3.0, -13.0, 12.0, 50),  # low below -supply
        (3.0, -3.0, 0.0, 50),  # zero supply
        (3.0, -3.0, 12.0, 0),  # no steps
        (3.0, -3.0, 12.0, 49),  # odd steps have no even halves
    ],
)
def test_invalid_square_inputs_are_rejected(high_volts, low_volts, supply_volts, steps):
    with pytest.raises(ValueError):
        square_profile(high_volts, low_volts, supply_volts, steps)


def test_constant_holds_one_signed_count_for_the_whole_cycle():
    assert constant_profile(volts=-6.0, supply_volts=12.0, steps=50) == [-2048] * 50


def test_constant_accepts_an_odd_number_of_steps():
    assert constant_profile(volts=0.0, supply_volts=12.0, steps=3) == [0, 0, 0]


@pytest.mark.parametrize(
    "volts, supply_volts, steps",
    [
        (-13.0, 12.0, 50),  # below -supply
        (-6.0, 0.0, 50),  # zero supply
        (-6.0, 12.0, 0),  # no steps
    ],
)
def test_invalid_constant_inputs_are_rejected(volts, supply_volts, steps):
    with pytest.raises(ValueError):
        constant_profile(volts, supply_volts, steps)


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
