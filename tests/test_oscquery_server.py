import json
import urllib.error
import urllib.request

import pytest

from jack.adapters.network.oscquery_server import start_oscquery_server
from jack.show.control.commands import SetValue, apply
from jack.show.control.control_board import ControlBoard
from tests.fakes import FakeClock
from tests.profiles import PROFILES


@pytest.fixture
def jack():
    board = ControlBoard(PROFILES, 0.5, "show", FakeClock())
    server = start_oscquery_server("127.0.0.1", 0, PROFILES, lambda: board.status(True), 9000)
    yield board, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def get(base, target):
    try:
        with urllib.request.urlopen(base + target, timeout=2) as response:
            return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def test_the_root_is_the_whole_namespace_as_json(jack):
    _, base = jack
    status, headers, body = get(base, "/")
    assert status == 200 and headers["Content-Type"] == "application/json"
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert json.loads(body)["CONTENTS"]["jack"]["CONTENTS"]["hand"]["FULL_PATH"] == "/jack/hand"


def test_a_value_query_reads_the_board_now(jack):
    board, base = jack
    apply(SetValue("pivot", 0.5, 0.5), board)
    assert json.loads(get(base, "/jack/pivot?VALUE")[2]) == {"VALUE": [0.5]}
    assert json.loads(get(base, "/jack/pivot?VALUE=")[2]) == {"VALUE": [0.5]}


def test_host_info(jack):
    _, base = jack
    info = json.loads(get(base, "/?HOST_INFO")[2])
    assert info["OSC_PORT"] == 9000 and info["OSC_TRANSPORT"] == "UDP"


def test_a_percent_encoded_path_and_a_fragment_are_handled(jack):
    _, base = jack
    assert json.loads(get(base, "/jack/%68and?FULL_PATH#x")[2]) == {"FULL_PATH": "/jack/hand"}


def test_errors_are_the_spec_status_codes(jack):
    _, base = jack
    assert get(base, "/jack/nope")[0] == 404
    assert get(base, "/jack/hand?NOPE")[0] == 400
    status, _, body = get(base, "/jack/rest?VALUE")
    assert status == 204 and body == b""


def test_a_failing_status_read_is_a_500_not_a_dropped_connection():
    def broken():
        raise RuntimeError("boom")

    server = start_oscquery_server("127.0.0.1", 0, PROFILES, broken, 9000)
    try:
        assert get(f"http://127.0.0.1:{server.server_address[1]}", "/")[0] == 500
    finally:
        server.shutdown()
        server.server_close()
