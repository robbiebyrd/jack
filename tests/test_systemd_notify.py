from motor_test.systemd_notify import notify, socket_address


def test_notify_sends_message_as_one_datagram(notify_socket):
    notify("READY=1")
    assert notify_socket.recv(64) == b"READY=1"


def test_notify_sends_each_watchdog_ping(notify_socket):
    notify("WATCHDOG=1")
    notify("WATCHDOG=1")
    assert notify_socket.recv(64) == b"WATCHDOG=1"
    assert notify_socket.recv(64) == b"WATCHDOG=1"


def test_notify_does_nothing_outside_systemd(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    notify("READY=1")  # must not raise


def test_at_prefix_maps_to_abstract_namespace():
    assert socket_address("@/org/freedesktop/systemd1/notify") == "\0/org/freedesktop/systemd1/notify"


def test_filesystem_path_is_used_as_is():
    assert socket_address("/run/systemd/notify") == "/run/systemd/notify"
