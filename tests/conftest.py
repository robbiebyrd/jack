import os
import shutil
import socket
import tempfile

import pytest


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
