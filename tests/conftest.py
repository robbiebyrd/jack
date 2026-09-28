import os
import shutil
import socket
import tempfile

import pytest


@pytest.fixture
def notify_socket(monkeypatch):
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "notify.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(path)
    server.settimeout(1)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    yield server
    server.close()
    shutil.rmtree(directory)
