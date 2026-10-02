import threading

import pytest

from pythonosc.udp_client import SimpleUDPClient

from motor_test.osc_server import start_osc_server


def serve(on_message=None):
    received = []
    arrived = threading.Event()

    def handle(address, args):
        if on_message:
            result = on_message(address, args)
        else:
            result = None
        received.append((address, args))
        arrived.set()
        return result

    server = start_osc_server("127.0.0.1", 0, handle)
    client = SimpleUDPClient("127.0.0.1", server.server_address[1])
    return server, client, received, arrived


def stop(server, client):
    server.shutdown()
    server.server_close()
    client._sock.close()


def test_messages_reach_the_handler_with_address_and_arguments():
    server, client, received, arrived = serve()
    try:
        client.send_message("/jack/hand/pose", ["curl", 2.0])
        assert arrived.wait(2.0)
        assert received == [("/jack/hand/pose", ["curl", 2.0])]
    finally:
        stop(server, client)


def test_a_non_osc_datagram_is_ignored_and_the_server_keeps_serving():
    """python-osc drops non-OSC datagrams before any handler runs."""
    server, client, received, arrived = serve()
    try:
        client._sock.sendto(b"not osc at all", ("127.0.0.1", server.server_address[1]))
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert received == [("/jack/rest", [])]
    finally:
        stop(server, client)


def test_a_handler_exception_does_not_stop_the_server(capsys):
    def explode_on_boom(address, args):
        if address == "/jack/boom":
            raise RuntimeError("handler failed")

    server, client, received, arrived = serve(explode_on_boom)
    try:
        client.send_message("/jack/boom", [])
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert ("/jack/rest", []) in received
    finally:
        stop(server, client)
    assert "RuntimeError" in capsys.readouterr().err


def test_a_handler_return_value_does_not_break_delivery():
    server, client, received, arrived = serve(lambda address, args: "ignored")
    try:
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert received == [("/jack/rest", [])]
        client._sock.settimeout(0.3)
        with pytest.raises(TimeoutError):
            client._sock.recv(4096)
    finally:
        stop(server, client)
