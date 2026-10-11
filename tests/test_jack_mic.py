import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
MIC = DEPLOY / "jack-mic.sh"


def fake_roc_send(tmp_path) -> dict[str, str]:
    """A PATH whose roc-send prints its arguments, one per line, and exits."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    roc_send = bin_dir / "roc-send"
    roc_send.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n")
    roc_send.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("JACK_MIC_")}
    return {**env, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}


def roc_send_arguments(env) -> list[str]:
    result = subprocess.run(["bash", str(MIC)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout.splitlines()


def test_the_mic_script_parses():
    result = subprocess.run(["bash", "-n", str(MIC)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_by_default_the_usb_microphone_streams_to_the_mac(tmp_path):
    assert roc_send_arguments(fake_roc_send(tmp_path)) == [
        "-i", "alsa://hw:S3",
        "-s", "rtp+rs8m://10.10.0.22:10011",
        "-r", "rs8m://10.10.0.22:10012",
        "-c", "rtcp://10.10.0.22:10013",
    ]


def test_the_receiver_device_and_ports_come_from_jack_env(tmp_path):
    env = {
        **fake_roc_send(tmp_path),
        "JACK_MIC_RECEIVER": "10.10.0.99",
        "JACK_MIC_DEVICE": "hw:1,0",
        "JACK_MIC_SOURCE_PORT": "20001",
        "JACK_MIC_REPAIR_PORT": "20002",
        "JACK_MIC_CONTROL_PORT": "20003",
    }
    assert roc_send_arguments(env) == [
        "-i", "alsa://hw:1,0",
        "-s", "rtp+rs8m://10.10.0.99:20001",
        "-r", "rs8m://10.10.0.99:20002",
        "-c", "rtcp://10.10.0.99:20003",
    ]
