import os
import subprocess
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy"
HEALTH = DEPLOY / "jack-health.sh"


def fake_pi(tmp_path, throttled="0x0", jack_status='{"mouth_mode": "live"}'):
    """A PATH with fake vcgencmd and curl, and a fake /proc, as the Pi would report them."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    vcgencmd = bin_dir / "vcgencmd"
    vcgencmd.write_text(
        "#!/bin/sh\ncase \"$1 $2\" in\n"
        f"  'get_throttled ') echo 'throttled={throttled}' ;;\n"
        "  'measure_volts core') echo 'volt=0.8800V' ;;\n"
        "  'measure_temp ') echo \"temp=51.1'C\" ;;\n"
        "  'measure_clock arm') echo 'frequency(48)=600000000' ;;\nesac\n"
    )
    curl = bin_dir / "curl"
    curl.write_text("#!/bin/sh\nexit 7\n" if jack_status is None else f"#!/bin/sh\nprintf '%s\\n' '{jack_status}'\n")
    for tool in (vcgencmd, curl):
        tool.chmod(0o755)
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    (proc / "loadavg").write_text("0.35 0.18 0.09 1/176 1317\n")
    (proc / "meminfo").write_text("MemTotal:        8007040 kB\nMemAvailable:    7786048 kB\n")
    (proc / "net" / "wireless").write_text(
        "Inter-| sta-|   Quality        |   Discarded packets               | Missed | WE\n"
        " face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22\n"
        " wlan0: 0000   62.  -48.  -256        0      0      0      0      0        0\n"
    )
    return {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "JACK_HEALTH_PROC": str(proc),
            "JACK_HEALTH_SAMPLES": "1"}


def sample(env) -> str:
    result = subprocess.run(["bash", str(HEALTH)], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout


def test_the_health_script_parses():
    result = subprocess.run(["bash", "-n", str(HEALTH)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_one_sample_is_one_line_with_every_reading(tmp_path):
    line = sample(fake_pi(tmp_path))
    assert line == (
        "health throttled=0x0 core=0.8800V temp=51.1'C arm_hz=600000000 load=0.35 mem_avail_kb=7786048 "
        'wifi_link=62 wifi_level=-48 jack={"mouth_mode": "live"}\n'
    )


def test_under_voltage_and_throttling_are_named(tmp_path):
    line = sample(fake_pi(tmp_path, throttled="0x50005"))
    assert "throttled=0x50005 UNDER-VOLTAGE-NOW THROTTLED-NOW under-voltage-since-boot throttled-since-boot " in line


def test_an_unreachable_jack_is_logged_as_such(tmp_path):
    assert sample(fake_pi(tmp_path, jack_status=None)).endswith(" jack=unreachable\n")
