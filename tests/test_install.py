import subprocess
from pathlib import Path

INSTALL = Path(__file__).resolve().parent.parent / "deploy" / "install.sh"


def test_install_script_parses():
    result = subprocess.run(["bash", "-n", str(INSTALL)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_install_script_sets_up_talking():
    script = INSTALL.read_text()
    packages = (
        "mumble-server", "python3-alsaaudio", "python3-opuslib", "python3-protobuf", "python3-venv",
        "roc-toolkit-tools",
    )
    for package in packages:
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


def test_the_journal_survives_reboots_so_a_crash_leaves_its_logs():
    script = INSTALL.read_text()
    assert "/etc/systemd/journald.conf.d/persistent.conf" in script
    assert "Storage=persistent" in script
    assert "systemctl restart systemd-journald" in script


def test_the_journal_reaches_the_sd_card_within_a_second():
    assert "SyncIntervalSec=1s" in INSTALL.read_text()


def test_the_health_log_is_installed_and_started():
    script = INSTALL.read_text()
    assert '"$REPO_DIR/deploy/jack-health.service"' in script
    assert "systemctl enable --now jack-health.service" in script


def test_kernel_crashes_are_kept_across_a_reset_and_reboot_the_pi():
    script = INSTALL.read_text()
    assert "dtoverlay=ramoops-pi4" in script
    assert "kernel.panic = 10" in script
