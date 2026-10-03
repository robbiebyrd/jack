import socket
import threading
import time

import pytest

from pythonosc.osc_bundle_builder import OscBundleBuilder
from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.udp_client import SimpleUDPClient

from jack.show.control.osc_feedback import Subscribers, state_messages
from jack.adapters.network.osc_server import OscEndpoint, start_feedback


def serve(on_message=None):
    received = []
    arrived = threading.Event()

    def handle(address, args, sender):
        if on_message:
            result = on_message(address, args)
        else:
            result = None
        received.append((address, args, sender))
        arrived.set()
        return result

    endpoint = OscEndpoint("127.0.0.1", 0)
    endpoint.serve(handle)
    client = SimpleUDPClient("127.0.0.1", endpoint.server_address[1])
    return endpoint, client, received, arrived


def stop(endpoint, client):
    endpoint.close()
    client._sock.close()


def recv_messages(sock, count, timeout=2.0):
    """Read `count` OSC datagrams from a UDP socket as (address, params)."""
    sock.settimeout(timeout)
    return [(m.address, m.params) for m in (OscMessage(sock.recv(65535)) for _ in range(count))]


def test_messages_reach_the_handler_with_address_arguments_and_sender():
    endpoint, client, received, arrived = serve()
    try:
        client.send_message("/jack/hand/pose", ["curl", 2.0])
        assert arrived.wait(2.0)
        assert received == [("/jack/hand/pose", ["curl", 2.0], ("127.0.0.1", client._sock.getsockname()[1]))]
    finally:
        stop(endpoint, client)


def test_a_non_osc_datagram_is_ignored_and_the_server_keeps_serving():
    """python-osc drops non-OSC datagrams before any handler runs."""
    endpoint, client, received, arrived = serve()
    try:
        client._sock.sendto(b"not osc at all", ("127.0.0.1", endpoint.server_address[1]))
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert [(a, args) for a, args, _ in received] == [("/jack/rest", [])]
    finally:
        stop(endpoint, client)


def test_a_handler_exception_does_not_stop_the_server(capsys):
    def explode_on_boom(address, args):
        if address == "/jack/boom":
            raise RuntimeError("handler failed")

    endpoint, client, received, arrived = serve(explode_on_boom)
    errors = ""
    try:
        client.send_message("/jack/boom", [])
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert "/jack/rest" in [a for a, _, _ in received]
        # The boom handler runs on its own thread, so its traceback can land after the rest arrives.
        deadline = time.monotonic() + 2.0
        while "RuntimeError" not in errors and time.monotonic() < deadline:
            errors += capsys.readouterr().err
            time.sleep(0.01)
    finally:
        stop(endpoint, client)
    assert "RuntimeError" in errors


def test_a_handler_return_value_does_not_break_delivery():
    endpoint, client, received, arrived = serve(lambda address, args: "ignored")
    try:
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert [(a, args) for a, args, _ in received] == [("/jack/rest", [])]
        client._sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            client._sock.recv(4096)
    finally:
        stop(endpoint, client)


def test_a_bundle_timed_in_the_future_is_delivered_at_once():
    endpoint, client, received, arrived = serve()
    try:
        bundle = OscBundleBuilder(time.time() + 5.0)
        bundle.add_content(OscMessageBuilder("/jack/rest").build())
        client.send(bundle.build())
        assert arrived.wait(1.0)
        assert [(a, args) for a, args, _ in received] == [("/jack/rest", [])]
    finally:
        stop(endpoint, client)


def test_request_threads_do_not_hold_the_process_open():
    endpoint = OscEndpoint("127.0.0.1", 0)
    try:
        assert endpoint._server.daemon_threads is True
    finally:
        endpoint.close()


def test_closing_an_endpoint_that_never_served_returns():
    endpoint = OscEndpoint("127.0.0.1", 0)
    closer = threading.Thread(target=endpoint.close, daemon=True)
    closer.start()
    closer.join(2.0)
    assert not closer.is_alive()


def test_send_delivers_messages_from_the_osc_port():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    try:
        endpoint.send(client.getsockname(), [("/jack/pong", []), ("/jack/hand/volts", [1.5])])
        client.settimeout(2.0)
        data, source = client.recvfrom(65535)
        assert source[1] == endpoint.server_address[1]
        assert OscMessage(data).address == "/jack/pong"
        assert recv_messages(client, 1) == [("/jack/hand/volts", [1.5])]
    finally:
        client.close()
        endpoint.close()


def test_packets_sent_before_serve_wait_in_the_socket_until_the_handler_is_installed():
    endpoint = OscEndpoint("127.0.0.1", 0)
    received = []
    arrived = threading.Event()

    def handle(address, args, sender):
        received.append(address)
        arrived.set()

    client = SimpleUDPClient("127.0.0.1", endpoint.server_address[1])
    try:
        client.send_message("/jack/early", [])
        assert not arrived.wait(0.2)
        endpoint.serve(handle)
        assert arrived.wait(2.0)
        assert received == ["/jack/early"]
    finally:
        client._sock.close()
        endpoint.close()


def feedback_snapshot():
    return {
        "mouth_mode": "live",
        "mumble_connected": True,
        "motors": {n: {"volts": 0.0, "max_hold_tripped": False} for n in ("mouth", "hand", "pivot", "elbow")},
    }


def test_feedback_thread_sends_the_full_set_to_a_subscriber_then_stops():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    subs = Subscribers(time.monotonic)
    snapshot = feedback_snapshot()
    stop_feedback = start_feedback(endpoint, subs, lambda: snapshot, lambda key, message: None, interval_s=0.01)
    try:
        subs.subscribe(client.getsockname())
        got = recv_messages(client, len(state_messages(snapshot)))
        assert got == [(address, args) for address, args in state_messages(snapshot)]
    finally:
        stop_feedback.set()
        client.close()
        endpoint.close()


class FailingTo:
    """An endpoint whose send raises `error` for one destination and delegates for the rest."""

    def __init__(self, endpoint, bad_destination, error=OSError("unreachable")):
        self._endpoint = endpoint
        self._bad = bad_destination
        self._error = error

    def send(self, destination, messages):
        if destination == self._bad:
            raise self._error
        self._endpoint.send(destination, messages)


@pytest.mark.parametrize("error", [OSError("unreachable"), ValueError("unreachable")])
def test_a_send_error_to_one_subscriber_is_logged_and_feedback_continues(error):
    endpoint = OscEndpoint("127.0.0.1", 0)
    bad = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    bad.bind(("127.0.0.1", 0))
    good = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    good.bind(("127.0.0.1", 0))
    subs = Subscribers(time.monotonic)
    snapshot = feedback_snapshot()
    logged = []
    logged_event = threading.Event()

    def log(key, message):
        logged.append((key, message))
        logged_event.set()

    subs.subscribe(bad.getsockname())
    subs.subscribe(good.getsockname())
    stop_feedback = start_feedback(
        FailingTo(endpoint, bad.getsockname(), error), subs, lambda: snapshot, log, interval_s=0.01
    )
    try:
        got = recv_messages(good, len(state_messages(snapshot)))
        assert got == [(address, args) for address, args in state_messages(snapshot)]
        assert logged_event.wait(2.0)
        assert str(bad.getsockname()) in logged[0][0]
        assert "unreachable" in logged[0][1]
    finally:
        stop_feedback.set()
        bad.close()
        good.close()
        endpoint.close()


def test_an_unexpected_error_in_a_feedback_cycle_is_logged_and_feedback_continues():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    subs = Subscribers(time.monotonic)
    snapshot = feedback_snapshot()
    calls = []
    logged = []

    def status():
        calls.append(1)
        if len(calls) == 1:
            raise KeyError("motors")
        return snapshot

    subs.subscribe(client.getsockname())
    stop_feedback = start_feedback(endpoint, subs, status, lambda key, message: logged.append((key, message)), interval_s=0.01)
    try:
        got = recv_messages(client, len(state_messages(snapshot)))
        assert got == [(address, args) for address, args in state_messages(snapshot)]
        assert logged == [("feedback loop", "OSC feedback cycle failed: KeyError: 'motors'")]
    finally:
        stop_feedback.set()
        client.close()
        endpoint.close()


def test_status_is_not_read_while_nobody_is_subscribed():
    endpoint = OscEndpoint("127.0.0.1", 0)
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    subs = Subscribers(time.monotonic)
    snapshot = feedback_snapshot()
    calls = []

    def status():
        calls.append(1)
        return snapshot

    stop_feedback = start_feedback(endpoint, subs, status, lambda key, message: None, interval_s=0.01)
    try:
        time.sleep(0.2)
        assert calls == []
        subs.subscribe(client.getsockname())
        recv_messages(client, len(state_messages(snapshot)))
        assert calls
    finally:
        stop_feedback.set()
        client.close()
        endpoint.close()
