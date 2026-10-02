"""HTTP show control on Python's ThreadingHTTPServer: JSON commands, /status and the control page.

See "HTTP" in SPEC.md. Request logging is off: a held slider on the control page sends about
20 requests a second, which would flood the journal.
"""

import json
import threading
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor_test.control_board import ControlBoard
from motor_test.poses import MotorProfile
from motor_test.show_commands import CommandError, apply, http_command

MAX_BODY_BYTES = 4096
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
        def do_GET(self) -> None:
            if self.path == "/":
                self._send(200, "text/html; charset=utf-8", page)
            elif self.path == "/status":
                self._json(200, board.status(mumble_connected()))
            else:
                self._json(404, {"error": f"no page at {self.path}"})

        def do_POST(self) -> None:
            try:
                apply(http_command(self.path, self._body(), profiles), board)
            except CommandError as error:
                self._json(error.status, {"error": str(error)})
                return
            self._json(200, {"ok": True})

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                raise CommandError(f"request body over {MAX_BODY_BYTES} bytes", 413)
            raw = self.rfile.read(length) if length else b""
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
