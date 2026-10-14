"""OSCQuery over HTTP on Python's ThreadingHTTPServer: GET a path below / for that part of the OSC address space.

`GET /` is the whole namespace, `GET /jack/hand?VALUE` one attribute, `GET /?HOST_INFO` the host. The
answers come from jack/show/control/osc_query.py. A separate port from the control page's HTTP, because
OSCQuery owns `/`. See "OSCQuery" in SPEC.md.
"""

import json
import logging
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from jack.show.control.osc_query import answer
from jack.show.control.status import Status
from jack.show.motion.poses import MotorProfile

log = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 5.0


def start_oscquery_server(
    host: str,
    port: int,
    profiles: Mapping[str, MotorProfile],
    status: Callable[[], Status],
    osc_port: int,
) -> ThreadingHTTPServer:
    """Serve OSCQuery on a daemon thread."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_S

        def do_GET(self) -> None:
            try:
                target = urlsplit(self.path)
                # `?VALUE` and `?VALUE=` name the same attribute.
                attribute = target.query.split("=", 1)[0] or None
                code, body = answer(unquote(target.path), attribute, profiles, status(), osc_port)
            except Exception as error:  # noqa: BLE001  # process boundary: any failure becomes a 500
                log.error("oscquery: GET %s: %s: %s", self.path, type(error).__name__, error)
                code, body = 500, None
            payload = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            if payload:
                self.send_header("Content-Type", "application/json")
            if code != 204:
                self.send_header("Content-Length", str(len(payload)))
            # Browser clients (such as oscqueryhtml) load the namespace from another origin.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="oscquery", daemon=True).start()
    return server
