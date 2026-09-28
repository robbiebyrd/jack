import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.fakes import install_recording_sleep, make_fake_leds

FLASH_SCRIPT = Path(__file__).resolve().parent.parent / "deploy" / "flash-leds.sh"


@pytest.fixture
def leds(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    leds_dir = make_fake_leds(tmp_path / "leds")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "JACK_LEDS_DIR": str(leds_dir),
        **install_recording_sleep(bin_dir, tmp_path / "sleep.log", tmp_path / "during.txt"),
    }
    return leds_dir, env, tmp_path


def flash(env, *args):
    return subprocess.run(["bash", str(FLASH_SCRIPT), *args], env=env, capture_output=True, text=True)


def test_both_leds_blink_during_the_flash(leds):
    leds_dir, env, tmp_path = leds
    result = flash(env)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "during.txt").read_text() == "timer\ntimer\n"
    for name in ("ACT", "PWR"):
        assert (leds_dir / name / "delay_on").read_text() == "100\n"
        assert (leds_dir / name / "delay_off").read_text() == "100\n"


def test_flash_lasts_ten_seconds_by_default(leds):
    _, env, tmp_path = leds
    flash(env)
    assert (tmp_path / "sleep.log").read_text() == "10\n"


def test_flash_length_can_be_given(leds):
    _, env, tmp_path = leds
    flash(env, "3")
    assert (tmp_path / "sleep.log").read_text() == "3\n"


def test_normal_triggers_are_restored_afterwards(leds):
    leds_dir, env, _ = leds
    flash(env)
    assert (leds_dir / "ACT" / "trigger").read_text() == "mmc0\n"
    assert (leds_dir / "PWR" / "trigger").read_text() == "default-on\n"


def test_normal_triggers_are_restored_when_the_flash_is_cut_short(leds):
    leds_dir, env, _ = leds
    result = flash({**env, "SLEEP_EXIT": "1"})
    assert result.returncode != 0
    assert (leds_dir / "ACT" / "trigger").read_text() == "mmc0\n"
    assert (leds_dir / "PWR" / "trigger").read_text() == "default-on\n"


def test_missing_led_fails_without_touching_the_others(leds):
    leds_dir, env, _ = leds
    shutil.rmtree(leds_dir / "PWR")
    result = flash(env)
    assert result.returncode != 0
    assert (leds_dir / "ACT" / "trigger").read_text() == "none [mmc0] timer heartbeat\n"
