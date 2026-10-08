"""Where things are on the Pi: the facts main.py and the hand-run tools share.

See SPEC.md "Hardware facts" and "Deployment".
"""

from pathlib import Path

I2C_BUS = 1
PWM_FREQ_HZ = 50
# The 3.5 mm jack; plughw converts formats if the card needs it (Task 1 of the talk plan checked it).
ALSA_DEVICE = "plughw:CARD=Headphones,DEV=0"
ALSA_PERIODS = 4
# mumble-server runs on the Pi itself; Boss's client connects to 10.10.0.54:64738.
MUMBLE_HOST = "127.0.0.1"
MUMBLE_PORT = 64738
MUMBLE_USER = "Jack"
# Repo defaults, then the Pi's override file (SPEC.md "Poses and per-motor settings").
REPO_ROOT = Path(__file__).resolve().parents[3]
POSES_PATHS = (REPO_ROOT / "poses.toml", Path("/etc/jack/poses.toml"))
