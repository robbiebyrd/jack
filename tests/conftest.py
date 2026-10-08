import os
import shutil
import socket
import tempfile
import time

import pytest

from jack.adapters.system import service_guard
from jack.adapters.system.logging_setup import RATE_LIMIT_S
from jack.support.rate_limit import RateLimit


@pytest.fixture
def notify_socket(monkeypatch):
    # tempfile.mkdtemp() (not pytest's tmp_path) keeps the AF_UNIX socket path short:
    # macOS limits these paths to about 104 bytes, and tmp_path can exceed that.
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(1)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield server
    server.close()
    shutil.rmtree(directory)


@pytest.fixture
def journal(caplog):
    """The captured log, rate-limited like the app's journal: `journal(clock)` installs the filter for this test.

    pytest reuses its capture handler across tests, so the filter is removed again at teardown.
    """
    installed = []

    def install(clock=time.monotonic):
        limiter = RateLimit(RATE_LIMIT_S, clock)
        caplog.handler.addFilter(limiter)
        installed.append(limiter)
        return caplog

    yield install
    for limiter in installed:
        caplog.handler.removeFilter(limiter)


@pytest.fixture
def unreadable_app_config(tmp_path, monkeypatch):
    """An app config directory this user can't enter, like /etc/jack to anyone outside the jack group."""
    config_dir = tmp_path / "etc-jack"
    config_dir.mkdir()
    monkeypatch.setattr(service_guard, "APP_CONFIG_DIR", config_dir)
    config_dir.chmod(0)
    try:
        yield config_dir / "poses.toml"
    finally:
        # Lets pytest delete tmp_path later.
        config_dir.chmod(0o755)
