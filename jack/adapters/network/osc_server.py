"""OSC over UDP for show control: receive commands, send replies and feedback.

The only module that imports python-osc.
"""

import logging
import threading
from collections.abc import Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_message_builder import OscMessageBuilder
from pythonosc.osc_server import ThreadingOSCUDPServer

from jack.show.control.osc_feedback import Destination, Message, Subscribers, state_messages
from jack.show.control.status import Status
from jack.support.rate_limit import rate_limited

log = logging.getLogger(__name__)

FEEDBACK_INTERVAL_S = 0.02


class OscEndpoint:
    """One UDP socket: commands arrive on it and replies and feedback leave from it."""

    def __init__(self, host: str, port: int):
        # Live show control: a future bundle timetag would otherwise sleep its handler thread until then.
        self._dispatcher = Dispatcher(strict_timing=False)
        self._server = ThreadingOSCUDPServer((host, port), self._dispatcher)
        # Per-packet threads must not keep the process alive after the talk loop exits.
        self._server.daemon_threads = True
        self._serving = False

    @property
    def server_address(self) -> Destination:
        return self._server.server_address

    def serve(self, handle: Callable[[str, list, Destination], None]) -> None:
        """Start serving on a daemon thread, passing each message's address, arguments and sender to `handle`."""

        def deliver(sender: Destination, address: str, *args: object) -> None:
            # python-osc replies to the sender with any non-None handler return value.
            handle(address, list(args), sender)

        self._dispatcher.set_default_handler(deliver, needs_reply_address=True)
        self._serving = True
        threading.Thread(target=self._server.serve_forever, name="osc", daemon=True).start()

    def send(self, destination: Destination, messages: list[Message]) -> None:
        for address, args in messages:
            builder = OscMessageBuilder(address=address)
            for arg in args:
                builder.add_arg(arg)
            self._server.socket.sendto(builder.build().dgram, destination)

    def close(self) -> None:
        # shutdown() waits for serve_forever to run, so it would hang on an endpoint that never served.
        if self._serving:
            self._server.shutdown()
        self._server.server_close()


def start_feedback(
    endpoint: OscEndpoint,
    subscribers: Subscribers,
    status: Callable[[], Status],
    interval_s: float = FEEDBACK_INTERVAL_S,
) -> threading.Event:
    """Every `interval_s`, send each subscriber what's due; returns an event that stops the thread."""
    stop = threading.Event()

    def run() -> None:
        while not stop.wait(interval_s):
            if len(subscribers) == 0:
                continue
            try:
                batches = subscribers.due(state_messages(status()))
            except Exception as error:  # noqa: BLE001
                # One bad cycle must not end feedback for the rest of the show.
                log.warning(
                    "OSC feedback cycle failed: %s: %s", type(error).__name__, error, **rate_limited("feedback loop")
                )
                continue
            for destination, messages in batches:
                try:
                    endpoint.send(destination, messages)
                except Exception as error:  # noqa: BLE001
                    # Whatever one destination raises must not stop the others or the thread.
                    log.warning(
                        "OSC feedback to %s failed: %s", destination, error, **rate_limited(f"feedback {destination}")
                    )

    threading.Thread(target=run, name="osc-feedback", daemon=True).start()
    return stop

