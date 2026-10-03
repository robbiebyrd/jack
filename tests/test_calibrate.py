import pytest

import calibrate


def test_refuses_while_the_app_is_running(monkeypatch, capsys):
    monkeypatch.setattr(calibrate, "app_is_running", lambda: True)
    assert calibrate.main(["elbow"]) == 1
    assert "Stop it first" in capsys.readouterr().err


def test_unknown_motor_is_a_usage_error():
    with pytest.raises(SystemExit) as exit_info:
        calibrate.main(["tail"])
    assert exit_info.value.code == 2


def test_invalid_poses_file_is_reported_before_touching_hardware(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(calibrate, "app_is_running", lambda: False)
    bad = tmp_path / "poses.toml"
    bad.write_text("[elbow]\nmax_v = 99\n")
    monkeypatch.setattr(calibrate, "POSES_PATHS", (calibrate.POSES_PATHS[0], bad))
    assert calibrate.main(["elbow"]) == 2
    assert "99" in capsys.readouterr().err


def test_unreadable_app_config_says_to_run_as_jack(monkeypatch, capsys, unreadable_app_config):
    monkeypatch.setattr(calibrate, "app_is_running", lambda: False)
    monkeypatch.setattr(calibrate, "POSES_PATHS", (calibrate.POSES_PATHS[0], unreadable_app_config))
    assert calibrate.main(["elbow"]) == 2
    assert "sudo -u jack" in capsys.readouterr().err
