"""Jack's OSC path over real UDP: endpoint, command handling, replies and feedback wired as main.py wires them."""

import socket
import time

from pythonosc.osc_message import OscMessage
from pythonosc.osc_message_builder import OscMessageBuilder

from jack.adapters.network.osc_server import OscEndpoint, start_feedback
from jack.show.control.control_board import ControlBoard
from jack.show.control.osc_feedback import Subscribers, state_messages
from jack.show.control.show_commands import OscContext, handle_osc
from jack.support.rate_limited_log import RateLimitedLog
from tests.profiles import PROFILES


def udp_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(2.0)
    return sock


def datagram(address, *args):
    builder = OscMessageBuilder(address=address)
    for arg in args:
        builder.add_arg(arg)
    return builder.build().dgram


def test_ping_is_answered_from_the_osc_port_and_subscribers_get_the_full_state():
    board = ControlBoard(PROFILES, 0.5, "live", time.monotonic)
    subscribers = Subscribers(time.monotonic)
    endpoint = OscEndpoint("127.0.0.1", 0)
    lines = []
    context = OscContext(
        profiles=PROFILES, board=board, subscribers=subscribers, send=endpoint.send,
        mumble_connected=lambda: True, reply_port=None, log=RateLimitedLog(lines.append, 60.0, time.monotonic),
    )
    endpoint.serve(lambda address, args, sender: handle_osc(address, args, sender, context))
    stop_feedback = start_feedback(
        endpoint, subscribers, lambda: board.status(True), lambda key, line: lines.append(line)
    )
    controller, listener = udp_socket(), udp_socket()
    osc_address = ("127.0.0.1", endpoint.server_address[1])
    try:
        controller.sendto(datagram("/jack/ping"), osc_address)
        data, source = controller.recvfrom(65535)
        assert OscMessage(data).address == "/jack/pong"
        assert source == osc_address

        controller.sendto(datagram("/jack/subscribe", listener.getsockname()[1]), osc_address)
        expected = state_messages(board.status(True))
        got = [(m.address, m.params) for m in (OscMessage(listener.recv(65535)) for _ in expected)]
        assert got == [(address, args) for address, args in expected]
    finally:
        stop_feedback.set()
        controller.close()
        listener.close()
        endpoint.close()
    assert lines == []
