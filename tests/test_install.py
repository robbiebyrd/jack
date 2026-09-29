import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"


def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_script_sets_up_talking():
    script = INSTALL.read_text()
    for package in ("mumble-server", "python3-alsaaudio", "python3-opuslib", "python3-protobuf", "python3-venv"):
        assert package in script
    assert "--system-site-packages" in script
    assert "--no-deps -r" in script
    assert "usermod --append --groups i2c,audio jack" in script
    assert "JACK_MUMBLE_PASSWORD=" in script


def test_a_failed_app_start_does_not_abort_the_installer():
    lines = INSTALL.read_text().splitlines()
    restart_lines = [line for line in lines if "restart jack.service" in line]
    assert len(restart_lines) == 1
    assert "||" in restart_lines[0]
    assert not any("enable --now jack.service" in line for line in lines)
