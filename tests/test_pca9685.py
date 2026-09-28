import pytest

from motor_test.pca9685 import Pca9685
from tests.fakes import RecordingBus

ADDRESS = 0x40


def make_chip():
    bus = RecordingBus()
    chip = Pca9685(bus, ADDRESS, pwm_freq_hz=50)
    return bus, chip


def test_init_resets_mode_and_sets_50hz_prescale_like_waveshare():
    bus, _ = make_chip()
    assert bus.writes == [
        (ADDRESS, 0x00, 0x00),  # MODE1 reset
        (ADDRESS, 0x00, 0x10),  # sleep to change prescale
        (ADDRESS, 0xFE, 121),  # prescale for 50 Hz
        (ADDRESS, 0x00, 0x00),  # wake
        (ADDRESS, 0x00, 0x80),  # restart
    ]


def test_set_off_count_writes_channel_registers():
    bus, chip = make_chip()
    bus.writes.clear()
    chip.set_off_count(5, 2048)
    assert bus.writes == [
        (ADDRESS, 0x1A, 0x00),
        (ADDRESS, 0x1B, 0x00),
        (ADDRESS, 0x1C, 0x00),
        (ADDRESS, 0x1D, 0x08),
    ]


def test_full_count_splits_across_off_low_and_high_registers():
    bus, chip = make_chip()
    bus.writes.clear()
    chip.set_off_count(0, 4095)
    assert bus.writes == [
        (ADDRESS, 0x06, 0x00),
        (ADDRESS, 0x07, 0x00),
        (ADDRESS, 0x08, 0xFF),
        (ADDRESS, 0x09, 0x0F),
    ]


@pytest.mark.parametrize("count", [-1, 4096])
def test_out_of_range_count_is_rejected_without_writing(count):
    bus, chip = make_chip()
    bus.writes.clear()
    with pytest.raises(ValueError):
        chip.set_off_count(5, count)
    assert bus.writes == []
