"""Minimal sd_notify client so systemd knows the app is ready and still alive."""

import os
import socket


def socket_address(path: str) -> str:
    """Translate systemd's `@name` notation into a Linux abstract socket address."""
    if path.startswith("@"):
        return "\0" + path[1:]
    return path


def notify(message: str) -> None:
    """Send one sd_notify message. Does nothing when not started by systemd."""
    path = os.environ.get("NOTIFY_SOCKET")
    if not path:
        return
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
        sock.sendto(message.encode(), socket_address(path))
