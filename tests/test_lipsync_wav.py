from types import SimpleNamespace

import lipsync_wav
from motor_test.talk_settings import TalkSettings
from tests.audio import write_wav


def test_defaults_are_the_starting_settings():
    args = lipsync_wav.build_parser().parse_args(["voice.wav"])
    assert lipsync_wav.settings_from_args(args) == TalkSettings()
    assert args.wav == "voice.wav"


def test_every_setting_can_be_overridden_from_the_command_line():
    args = lipsync_wav.build_parser().parse_args(
        ["voice.wav", "--gate-open-db", "-30", "--release-s", "0.1", "--mouth-lead-ms", "40"]
    )
    settings = lipsync_wav.settings_from_args(args)
    assert (settings.gate_open_db, settings.release_s, settings.mouth_lead_ms) == (-30.0, 0.1, 40.0)


def test_refuses_while_the_app_is_running(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: True)
    assert lipsync_wav.main(["voice.wav"]) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_impossible_override_is_rejected_before_touching_hardware(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main(["voice.wav", "--open-min-v", "3", "--open-max-v", "2"]) == 2
    assert "open_max_v" in capsys.readouterr().err


def test_override_above_the_supply_is_rejected_before_touching_hardware(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main(["voice.wav", "--open-max-v", "20"]) == 2
    assert "supply" in capsys.readouterr().err


def test_missing_wav_is_rejected_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main([str(tmp_path / "missing.wav")]) == 2
    assert "missing.wav" in capsys.readouterr().err


def test_wrong_format_wav_is_rejected_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    path = write_wav(tmp_path / "stereo.wav", bytes(40), channels=2)
    assert lipsync_wav.main([str(path)]) == 2
    assert "mono 16-bit 48000 Hz" in capsys.readouterr().err


def test_runs_on_for_the_tail_after_the_wav_ends():
    source = SimpleNamespace(finished=False)
    until = lipsync_wav.after_tail(source, tail_ticks=2)
    assert not until()
    source.finished = True
    assert [until(), until(), until()] == [False, False, True]
