import http.client
import json
import socket
import time
import urllib.error
import urllib.request

import pytest

from motor_test.control_board import ControlBoard
from motor_test import http_server
from motor_test.http_server import MAX_BODY_BYTES, start_http_server
from tests.fakes import FakeClock
from tests.profiles import PROFILES


@pytest.fixture
def jack():
    board = ControlBoard(PROFILES, 0.5, "live", FakeClock())
    server = start_http_server("127.0.0.1", 0, board, PROFILES, lambda: True)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield board, base
    server.shutdown()
    server.server_close()


def request(base, path, body=None, raw=None):
    data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
    req = urllib.request.Request(base + path, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read()


def test_control_page_is_served(jack):
    _, base = jack
    status, kind, body = request(base, "/")
    assert status == 200 and kind.startswith("text/html")
    assert b"<title>" in body and b"/status" in body
    assert b"lostpointercapture" in body and b"visibilitychange" in body
    assert b"innerHTML" not in body


def test_status_is_json(jack):
    _, base = jack
    status, kind, body = request(base, "/status")
    data = json.loads(body)
    assert status == 200 and kind == "application/json"
    assert data["mouth_mode"] == "live" and data["mumble_connected"] is True
    assert set(data["motors"]) == {"mouth", "hand", "pivot", "elbow"}


def test_commands_drive_the_board(jack):
    board, base = jack
    assert request(base, "/hand", {"value": 1.0})[0] == 200
    assert board.target_volts("hand") == 2.0
    assert request(base, "/elbow/pose", {"name": "up"})[0] == 200
    assert board.target_volts("elbow") == 2.0
    assert request(base, "/rest", {})[0] == 200
    assert board.target_volts("hand") is None
    assert request(base, "/mouth/mode", {"mode": "show"})[0] == 200
    assert board.mouth_mode == "show"


def test_empty_body_is_allowed_for_rest(jack):
    _, base = jack
    assert request(base, "/hand/rest", raw=b"")[0] == 200


@pytest.mark.parametrize(
    "path, body, raw, status",
    [
        ("/tail", {"value": 1}, None, 404),
        ("/hand/pose", {"name": "wave"}, None, 404),
        ("/hand", {"value": "x"}, None, 400),
        ("/hand", None, b"{not json", 400),
        ("/hand", None, b"[1, 2]", 400),
        ("/mouth", {"value": 0.5}, None, 409),
        ("/hand", None, b"{" + b" " * MAX_BODY_BYTES + b"}", 413),
    ],
)
def test_errors_are_json_with_a_status(jack, path, body, raw, status):
    _, base = jack
    code, kind, payload = request(base, path, body, raw)
    assert code == status and kind == "application/json"
    assert "error" in json.loads(payload)


def test_unknown_get_is_404(jack):
    _, base = jack
    assert request(base, "/nope")[0] == 404


@pytest.mark.parametrize("length", ["abc", "-5"])
def test_bad_content_length_is_400(jack, length):
    _, base = jack
    connection = http.client.HTTPConnection(base.removeprefix("http://"), timeout=2)
    connection.putrequest("POST", "/hand")
    connection.putheader("Content-Length", length)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 400
    assert "Content-Length" in json.loads(response.read())["error"]
    connection.close()


class BrokenBoard:
    def status(self, mumble_connected):
        raise RuntimeError("boom")


def test_unexpected_error_is_a_json_500_and_the_server_keeps_serving(capsys):
    server = start_http_server("127.0.0.1", 0, BrokenBoard(), PROFILES, lambda: True)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        code, kind, payload = request(base, "/status")
        assert code == 500 and kind == "application/json"
        assert "RuntimeError" in json.loads(payload)["error"]
        assert request(base, "/")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
    assert "RuntimeError" in capsys.readouterr().err


def read_until_closed(sock, deadline_s):
    """Everything the server sends until it closes the connection; fails if that takes past `deadline_s`."""
    sock.settimeout(deadline_s)
    started, received = time.monotonic(), b""
    while chunk := sock.recv(4096):
        received += chunk
    assert time.monotonic() - started < deadline_s
    return received


@pytest.fixture
def short_timeout_server(monkeypatch):
    monkeypatch.setattr(http_server, "REQUEST_TIMEOUT_S", 0.5)
    board = ControlBoard(PROFILES, 0.5, "live", FakeClock())
    server = start_http_server("127.0.0.1", 0, board, PROFILES, lambda: True)
    yield server.server_address
    server.shutdown()
    server.server_close()


def test_a_body_that_never_arrives_times_out_with_a_json_408(short_timeout_server):
    with socket.create_connection(short_timeout_server) as sock:
        sock.sendall(b"POST /hand HTTP/1.1\r\nHost: jack\r\nContent-Length: 10\r\n\r\n")
        response = read_until_closed(sock, 0.5 + 2.0)
    head, _, body = response.partition(b"\r\n\r\n")
    assert head.split(b" ")[1] == b"408"
    assert "error" in json.loads(body)


def test_a_silent_connection_is_closed_after_the_timeout(short_timeout_server):
    with socket.create_connection(short_timeout_server) as sock:
        assert read_until_closed(sock, 0.5 + 2.0) == b""
