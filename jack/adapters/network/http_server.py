"""HTTP show control on Python's ThreadingHTTPServer: JSON commands, /status and the control page.

See "HTTP" in SPEC.md. Request logging is off: a held slider on the control page sends about
20 requests a second, which would flood the journal.
"""

import json
import sys
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jack.show.control.commands import CommandError, apply
from jack.show.control.control_board import ControlBoard
from jack.show.control.routes import command_for
from jack.show.motion.poses import MotorProfile

MAX_BODY_BYTES = 4096
# A phone dropping Wi-Fi mid-request must not hold a server thread forever.
REQUEST_TIMEOUT_S = 5.0
_PAGE_PATH = Path(__file__).resolve().parent / "control_page.html"


def start_http_server(
    host: str,
    port: int,
    board: ControlBoard,
    profiles: Mapping[str, MotorProfile],
    mumble_connected: Callable[[], bool],
) -> ThreadingHTTPServer:
    """Serve HTTP on a daemon thread."""
    page = _PAGE_PATH.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        timeout = REQUEST_TIMEOUT_S

        def do_GET(self) -> None:
            self._respond(self._get)

        def do_POST(self) -> None:
            self._respond(self._post)

        def _get(self) -> None:
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif self.path == "/status":
                self._json(200, board.status(mumble_connected()).to_json())
            else:
                self._json(404, {"error": f"no page at {self.path}"})

        def _post(self) -> None:
            apply(command_for(self.path, self._body(), profiles), board)
            self._json(200, {"ok": True})

        def _respond(self, handle: Callable[[], None]) -> None:
            """Turn any failure into a JSON error so the connection is never dropped."""
            try:
                handle()
            except CommandError as error:
                self._json(error.status, {"error": str(error)})
            except Exception as error:  # noqa: BLE001  # process boundary: any failure becomes a 500
                message = f"internal error: {type(error).__name__}: {error}".replace("\n", " ")
                print(f"http: {self.command} {self.path}: {message}", file=sys.stderr)  # noqa: T201
                self._json(500, {"error": message})

        def _content_length(self) -> int:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                raise CommandError("Content-Length must be a non-negative integer")
            return length

        def _body(self) -> dict:
            length = self._content_length()
            if length > MAX_BODY_BYTES:
                raise CommandError(f"request body over {MAX_BODY_BYTES} bytes", 413)
            try:
                raw = self.rfile.read(length) if length else b""
            except TimeoutError as error:
                raise CommandError(f"request body did not arrive within {REQUEST_TIMEOUT_S} s", 408) from error
            if not raw.strip():
                return {}
            try:
                body = json.loads(raw)
            except ValueError as error:
                raise CommandError(f"body is not JSON: {error}") from error
            if not isinstance(body, dict):
                raise CommandError("body must be a JSON object")
            return body

        def _json(self, status: int, data: dict) -> None:
            self._send(status, "application/json", json.dumps(data).encode())

        def _send(self, status: int, content_type: str, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    return server
