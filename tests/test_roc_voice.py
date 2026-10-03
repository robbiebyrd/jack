import os
import sys
import time
from array import array

import pytest

from jack.adapters.audio.roc_voice import RocVoice, roc_recv_command
from jack.show.audio.pcm import FRAME_SAMPLES
from tests.audio import constant_frame


def stereo(left: int, right: int, frames: int = 1) -> bytes:
    """Stereo s16 samples as roc-recv writes them: left/right pairs."""
    return array("h", [left, right] * (FRAME_SAMPLES * frames)).tobytes()


def fake_roc_recv(tmp_path, body: str) -> list[str]:
    """An executable Python script standing in for roc-recv; `out` is its binary stdout.

    It is run directly (not via `python script`), so deleting it makes starting it fail the way a
    missing roc-recv does.
    """
    script = tmp_path / "fake-roc-recv"
    script.write_text(f"#!{sys.executable}\nimport os, signal, sys, time\nout = sys.stdout.buffer\n" + body)
    script.chmod(0o755)
    return [str(script)]


def wait_for(predicate, timeout_s: float = 3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if (value := predicate()):
            return value
        time.sleep(0.01)
    raise AssertionError("timed out")


@pytest.fixture
def voices():
    """Every RocVoice a test makes is closed afterwards, so no child process or pipe outlives it."""
    made = []

    def make(command, log=None, **kwargs):
        voice = RocVoice(command, 50, (log if log is not None else []).append, **kwargs)
        made.append(voice)
        return voice

    yield make
    for voice in made:
        voice.close()


def test_the_command_receives_on_the_settings_ports_as_raw_stereo_at_48_khz():
    assert roc_recv_command(10001, 10002, 10003, 100) == [
        "roc-recv",
        "-s", "rtp+rs8m://0.0.0.0:10001",
        "-r", "rs8m://0.0.0.0:10002",
        "-c", "rtcp://0.0.0.0:10003",
        "-o", "file:-",
        "--output-format", "s16",
        "--rate", "48000",
        "--target-latency=100ms",
    ]


def test_a_fractional_latency_is_passed_as_written():
    assert roc_recv_command(1, 2, 3, 87.5)[-1] == "--target-latency=87.5ms"


def test_nothing_received_gives_no_frames(tmp_path, voices):
    voice = voices(fake_roc_recv(tmp_path, "time.sleep(30)\n"))
    voice.start()
    assert voice.take_frames() == []


def test_stereo_output_becomes_mono_frames_from_the_left_channel(tmp_path, voices):
    body = f"out.write({stereo(5, 99) + stereo(7, 99)!r}); out.flush(); time.sleep(30)\n"
    voice = voices(fake_roc_recv(tmp_path, body))
    voice.start()
    assert wait_for(voice.take_frames) == [constant_frame(5)]
    assert voice.take_frames() == [constant_frame(7)]
    assert voice.take_frames() == []


def test_output_split_at_any_byte_still_gives_exact_frames(tmp_path, voices):
    data = stereo(3, -3) + stereo(4, -4)
    body = (
        f"data = {data!r}\nfor i in range(0, len(data), 7):\n"
        "    out.write(data[i:i + 7]); out.flush()\ntime.sleep(30)\n"
    )
    voice = voices(fake_roc_recv(tmp_path, body))
    voice.start()
    assert wait_for(voice.take_frames) == [constant_frame(3)]
    assert wait_for(voice.take_frames) == [constant_frame(4)]


def test_digital_silence_is_not_handed_to_the_talk_loop(tmp_path, voices):
    body = f"out.write({stereo(0, 0, frames=3) + stereo(9, 9)!r}); out.flush(); time.sleep(30)\n"
    voice = voices(fake_roc_recv(tmp_path, body))
    voice.start()
    assert wait_for(voice.take_frames) == [constant_frame(9)]


def test_a_missing_program_fails_at_start():
    voice = RocVoice(["/nonexistent/roc-recv"], 50, print)
    with pytest.raises(FileNotFoundError):
        voice.start()
    voice.close()


def test_an_exited_roc_recv_is_restarted_and_logged_once_a_minute(tmp_path, voices):
    runs = tmp_path / "runs"
    log = []
    body = f"open({str(runs)!r}, 'a').write('x')\nsys.exit(3)\n"
    voice = voices(fake_roc_recv(tmp_path, body), log, restart_delay_s=0.01)
    voice.start()
    wait_for(lambda: runs.exists() and len(runs.read_text()) >= 3)
    assert log == ["roc-recv exited (code 3); restarting"]


def test_a_program_that_vanishes_between_runs_keeps_being_retried(tmp_path, voices):
    runs = tmp_path / "runs"
    log = []
    body = f"open({str(runs)!r}, 'a').write('x')\nos.remove(__file__)\nsys.exit(0)\n"
    command = fake_roc_recv(tmp_path, body)
    voice = voices(command, log, restart_delay_s=0.01)
    voice.start()
    wait_for(lambda: len(log) >= 2)
    assert log[0] == "roc-recv exited (code 0); restarting"
    assert log[1].startswith("roc-recv could not start")
    assert voice._thread.is_alive()


def test_close_stops_a_running_roc_recv(tmp_path, voices):
    pid_file = tmp_path / "pid"
    body = f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n"
    voice = voices(fake_roc_recv(tmp_path, body))
    voice.start()
    pid = int(wait_for(lambda: pid_file.exists() and pid_file.read_text()))
    started = time.monotonic()
    voice.close()
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_close_kills_a_roc_recv_that_ignores_sigterm(tmp_path, voices):
    pid_file = tmp_path / "pid"
    body = (
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(30)\n"
    )
    voice = voices(fake_roc_recv(tmp_path, body), stop_grace_s=0.2)
    voice.start()
    pid = int(wait_for(lambda: pid_file.exists() and pid_file.read_text()))
    voice.close()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_close_is_safe_to_call_twice(tmp_path, voices):
    voice = voices(fake_roc_recv(tmp_path, "time.sleep(30)\n"))
    voice.start()
    voice.close()
    voice.close()
