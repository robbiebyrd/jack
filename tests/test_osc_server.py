import threading

from pythonosc.udp_client import SimpleUDPClient

from motor_test.osc_server import start_osc_server


def serve():
    received = []
    arrived = threading.Event()

    def handle(address, args):
        received.append((address, args))
        arrived.set()

    server = start_osc_server("127.0.0.1", 0, handle)
    client = SimpleUDPClient("127.0.0.1", server.server_address[1])
    return server, client, received, arrived


def test_messages_reach_the_handler_with_address_and_arguments():
    server, client, received, arrived = serve()
    try:
        client.send_message("/jack/hand/pose", ["curl", 2.0])
        assert arrived.wait(2.0)
        assert received == [("/jack/hand/pose", ["curl", 2.0])]
    finally:
        server.shutdown()
        server.server_close()


def test_a_garbage_packet_does_not_stop_the_server():
    server, client, received, arrived = serve()
    try:
        client._sock.sendto(b"not osc at all", ("127.0.0.1", server.server_address[1]))
        client.send_message("/jack/rest", [])
        assert arrived.wait(2.0)
        assert received == [("/jack/rest", [])]
    finally:
        server.shutdown()
        server.server_close()
