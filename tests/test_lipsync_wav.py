from types import SimpleNamespace

import lipsync_wav
from jack.show.audio.talk_settings import TalkSettings
from tests.audio import write_wav


def test_defaults_are_the_starting_settings():
    args = lipsync_wav.build_parser().parse_args(["voice.wav"])
    assert lipsync_wav.settings_from_args(args) == TalkSettings()
    assert args.wav == "voice.wav"


def test_every_setting_can_be_overridden_from_the_command_line():
    args = lipsync_wav.build_parser().parse_args(
        ["voice.wav", "--mouth-gain-db", "20", "--release-s", "0.1", "--mouth-lead-ms", "40"]
    )
    settings = lipsync_wav.settings_from_args(args)
    assert (settings.mouth_gain_db, settings.release_s, settings.mouth_lead_ms) == (20.0, 0.1, 40.0)


def test_refuses_while_the_app_is_running(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: True)
    assert lipsync_wav.main(["voice.wav"]) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_impossible_override_is_rejected_before_touching_hardware(monkeypatch, capsys):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    assert lipsync_wav.main(["voice.wav", "--attack-s", "0"]) == 2
    assert "attack_s" in capsys.readouterr().err


def test_invalid_poses_file_is_rejected_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    bad = tmp_path / "poses.toml"
    bad.write_text("[mouth]\nmax_v = 99\n")
    monkeypatch.setattr(lipsync_wav, "POSES_PATHS", (lipsync_wav.POSES_PATHS[0], bad))
    assert lipsync_wav.main(["voice.wav"]) == 2
    assert "99" in capsys.readouterr().err


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


def test_unreadable_app_config_says_to_run_as_jack(monkeypatch, capsys, unreadable_app_config):
    monkeypatch.setattr(lipsync_wav, "app_is_running", lambda: False)
    monkeypatch.setattr(lipsync_wav, "POSES_PATHS", (lipsync_wav.POSES_PATHS[0], unreadable_app_config))
    assert lipsync_wav.main(["voice.wav"]) == 2
    assert "sudo -u jack" in capsys.readouterr().err
