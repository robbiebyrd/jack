import pytest

from motor_test.ramp import PWM_MAX_COUNT, ramp_profile, square_profile


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
