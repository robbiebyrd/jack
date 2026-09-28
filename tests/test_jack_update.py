import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.fakes import install_recording_sleep, make_fake_leds

REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATE_SCRIPT = REPO_ROOT / "deploy" / "jack-update.sh"
FLASH_SCRIPT = REPO_ROOT / "deploy" / "flash-leds.sh"
IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(cwd, *args):
    result = subprocess.run(["git", *IDENTITY, *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@dataclass
class Deployment:
    origin: Path
    work: Path
    checkout: Path
    systemctl_log: Path
    leds_dir: Path
    sleep_log: Path
    env: dict

    def commit_and_push(self, relative_path, content):
        path = self.work / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(self.work, "add", relative_path)
        git(self.work, "commit", "-m", f"update {relative_path}")
        git(self.work, "push", "origin", "main")
        return git(self.work, "rev-parse", "HEAD")

    def run_update(self):
        return subprocess.run(
            ["bash", str(self.checkout / "deploy" / "jack-update.sh")],
            env=self.env,
            capture_output=True,
            text=True,
        )

    def restarts(self):
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()

    def flashes(self):
        if not self.sleep_log.exists():
            return []
        return self.sleep_log.read_text().splitlines()


@pytest.fixture
def deployment(tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "checkout", "-B", "main")
    (work / "deploy").mkdir()
    shutil.copy(UPDATE_SCRIPT, work / "deploy" / "jack-update.sh")
    shutil.copy(FLASH_SCRIPT, work / "deploy" / "flash-leds.sh")
    (work / "main.py").write_text("print('v1')\n")
    git(work, "add", ".")
    git(work, "commit", "-m", "initial")
    git(work, "push", "-u", "origin", "main")

    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", str(origin), str(checkout))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl = bin_dir / "systemctl"
    systemctl.write_text('#!/bin/sh\necho "$@" >> "$SYSTEMCTL_LOG"\n')
    systemctl.chmod(0o755)
    systemctl_log = tmp_path / "systemctl.log"
    leds_dir = make_fake_leds(tmp_path / "leds")
    sleep_log = tmp_path / "sleep.log"

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "JACK_REPO_DIR": str(checkout),
        "SYSTEMCTL_LOG": str(systemctl_log),
        "JACK_LEDS_DIR": str(leds_dir),
        **install_recording_sleep(bin_dir, sleep_log, tmp_path / "leds-during-sleep.txt"),
    }
    return Deployment(origin, work, checkout, systemctl_log, leds_dir, sleep_log, env)


def test_up_to_date_checkout_is_left_alone(deployment):
    before = git(deployment.checkout, "rev-parse", "HEAD")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == before
    assert deployment.restarts() == []


def test_new_commit_is_deployed_and_app_restarted_once(deployment):
    new_head = deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    assert (deployment.checkout / "main.py").read_text() == "print('v2')\n"
    assert deployment.restarts() == ["try-restart jack.service"]


def test_second_run_after_deploy_does_not_restart_again(deployment):
    deployment.commit_and_push("main.py", "print('v2')\n")
    deployment.run_update()
    deployment.run_update()
    assert deployment.restarts() == ["try-restart jack.service"]


def test_local_edits_on_the_pi_are_discarded(deployment):
    (deployment.checkout / "main.py").write_text("print('hand edit')\n")
    deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert (deployment.checkout / "main.py").read_text() == "print('v2')\n"


def test_force_pushed_history_is_followed(deployment):
    (deployment.work / "main.py").write_text("print('rewritten')\n")
    git(deployment.work, "commit", "--amend", "-am", "rewritten initial")
    git(deployment.work, "push", "--force", "origin", "main")
    rewritten = git(deployment.work, "rev-parse", "HEAD")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == rewritten
    assert deployment.restarts() == ["try-restart jack.service"]


def test_fetch_failure_leaves_checkout_and_app_untouched(deployment):
    before = git(deployment.checkout, "rev-parse", "HEAD")
    shutil.rmtree(deployment.origin)
    result = deployment.run_update()
    assert result.returncode != 0
    assert git(deployment.checkout, "rev-parse", "HEAD") == before
    assert deployment.restarts() == []


def test_commit_that_rewrites_the_update_script_completes_cleanly(deployment):
    script = (deployment.work / "deploy" / "jack-update.sh").read_text()
    shebang, rest = script.split("\n", 1)
    padded = shebang + "\n" + "# padding to shift byte offsets\n" * 200 + rest
    new_head = deployment.commit_and_push("deploy/jack-update.sh", padded)
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    assert deployment.restarts() == ["try-restart jack.service"]


def test_deploy_flashes_the_leds_for_ten_seconds_then_restores_them(deployment):
    deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert deployment.flashes() == ["10"]
    assert (deployment.leds_dir / "ACT" / "trigger").read_text() == "mmc0\n"
    assert (deployment.leds_dir / "PWR" / "trigger").read_text() == "default-on\n"


def test_poll_without_a_new_commit_does_not_flash(deployment):
    deployment.run_update()
    assert deployment.flashes() == []


def test_led_failure_does_not_fail_the_deploy(deployment):
    shutil.rmtree(deployment.leds_dir)
    new_head = deployment.commit_and_push("main.py", "print('v2')\n")
    result = deployment.run_update()
    assert result.returncode == 0, result.stderr
    assert git(deployment.checkout, "rev-parse", "HEAD") == new_head
    assert deployment.restarts() == ["try-restart jack.service"]
    assert "LED flash failed" in result.stdout
