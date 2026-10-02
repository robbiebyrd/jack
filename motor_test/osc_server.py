"""OSC over UDP into show control; the only module that imports python-osc."""

import threading
from collections.abc import Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer


def start_osc_server(host: str, port: int, handle: Callable[[str, list], None]) -> ThreadingOSCUDPServer:
    """Serve OSC on a daemon thread, passing every message's address and arguments to `handle`."""
    dispatcher = Dispatcher()

    def deliver(address: str, *args) -> None:
        # python-osc replies to the sender with any non-None handler return value.
        handle(address, list(args))

    dispatcher.set_default_handler(deliver)
    server = ThreadingOSCUDPServer((host, port), dispatcher)
    threading.Thread(target=server.serve_forever, name="osc", daemon=True).start()
    return server
