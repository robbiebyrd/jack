import os
import shutil
import socket
import tempfile

import pytest

from jack.adapters.system import service_guard


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
