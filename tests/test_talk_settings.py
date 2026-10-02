import dataclasses

import pytest

from motor_test.talk_settings import TalkSettings


def test_starting_values_match_the_spec():
    settings = TalkSettings()
    assert settings.open_curve == 2.0
    assert (settings.stall_v, settings.max_stall_s) == (5.0, 0.5)
    assert (settings.attack_s, settings.release_s) == (0.01, 0.04)
    assert (settings.gate_open_db, settings.gate_close_db, settings.full_db) == (-22.0, -27.0, -14.0)
    assert (settings.mouth_lead_ms, settings.max_backlog_ms) == (0.0, 200.0)
    assert not hasattr(settings, "open_min_v")


def test_durations_convert_to_whole_20_ms_ticks():
    settings = TalkSettings(mouth_lead_ms=40.0)
    assert settings.max_stall_ticks == 25
    assert settings.mouth_lead_ticks == 2
    assert settings.max_backlog_frames == 10


def test_no_mouth_lead_is_zero_ticks():
    assert TalkSettings().mouth_lead_ticks == 0


def test_settings_are_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        TalkSettings().stall_v = 2.0


@pytest.mark.parametrize(
    "overrides",
    [
        {"open_curve": 0.5},
        {"stall_v": 0.0},
        {"attack_s": 0.0},
        {"release_s": -0.08},
        {"gate_close_db": -20.0},
        {"full_db": -40.0},
        {"max_stall_s": 0.0},
        {"mouth_lead_ms": 30.0},
        {"mouth_lead_ms": -20.0},
        {"max_backlog_ms": 0.0},
    ],
)
def test_impossible_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        TalkSettings(**overrides)
