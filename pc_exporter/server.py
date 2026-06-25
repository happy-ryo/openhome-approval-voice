#!/usr/bin/env python3
"""Minimal one-way HTTP delivery for the approval queue (Refs #7).

This is the (b) half of the exporter. It serves the queue JSON FILE that
core.export_queue() wrote -- it imports no DB code and reads the file fresh on
every GET, so periodic re-exports are visible to clients. There is no
write/receive path (one-way invariant): only GET /announce_queue.json is
served; any other path is 404 and any non-GET method is 405.

HTTP envelope (FIXED -- must match the PR2 ability exactly):
  GET /announce_queue.json -> 200, Content-Type: application/json,
      body = a JSON array of section 1.3 AnnounceItem objects.

Port: env APPROVAL_VOICE_HTTP_PORT (default 8731). Bind host default 0.0.0.0
(LAN delivery).

PC side, NOT bundled into the DevKit ability (the do_POST/do_PUT handlers below
only exist to RETURN 405 -- they are not an input path; the sandbox one-way
scanner is scoped to approval_voice/ + openhome_ability/ and does not cover this
package).

All print/log strings here stay ASCII (cp932 console safety); queue values
(which may be Japanese) are served as raw utf-8 file bytes and never printed.
"""
from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8731
DEFAULT_HOST = "0.0.0.0"
QUEUE_ROUTE = "/announce_queue.json"


def env_port() -> int:
    """Resolve the serve port from APPROVAL_VOICE_HTTP_PORT (default 8731)."""
    raw = os.environ.get("APPROVAL_VOICE_HTTP_PORT")
    if raw is None or raw.strip() == "":
        return DEFAULT_PORT
    return int(raw)


def make_handler(queue_path: Path):
    """Build a request handler class bound to ``queue_path``.

    The handler reads the file fresh on each GET (single open-read-close), so a
    concurrent atomic re-export (os.replace) is picked up on the next request
    and the read window stays tiny.
    """
    queue_path = Path(queue_path)

    class _Handler(BaseHTTPRequestHandler):
        # Silence the default stderr access log (keeps output ASCII / quiet).
        def log_message(self, fmt, *args):  # noqa: D401, N802
            return

        def _send_plain(self, code: int, text: str) -> None:
            body = text.encode("ascii", "replace")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path != QUEUE_ROUTE:
                self._send_plain(404, "not found")
                return
            try:
                body = queue_path.read_bytes()
            except OSError:
                # FileNotFoundError: the queue file has not been exported yet.
                # PermissionError / other OSError (Windows): the read can briefly
                # collide with the re-export's os.replace (CPython's read open()
                # lacks FILE_SHARE_DELETE -> ERROR_SHARING_VIOLATION). Either way,
                # serve an empty section 1.3 array so a transient collision never
                # yields a 500; the next poll (~interval) picks up the new file.
                body = b"[]"
            self.send_response(200)
            # Bare 'application/json' -- no charset suffix (matches PR2).
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # Any non-GET method -> 405 (one-way invariant: no write path).
        def _reject_method(self):
            self._send_plain(405, "method not allowed")

        do_POST = _reject_method
        do_PUT = _reject_method
        do_DELETE = _reject_method
        do_PATCH = _reject_method
        do_HEAD = _reject_method
        do_OPTIONS = _reject_method

    return _Handler


def make_server(queue_path: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create (but do not serve) a ThreadingHTTPServer bound to host:port.

    Pass ``port=0`` for an ephemeral port (tests read ``server.server_address``
    for the assigned port).
    """
    handler = make_handler(Path(queue_path))
    return ThreadingHTTPServer((host, port), handler)


def serve(queue_path: Path, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Bind and serve forever. Blocks the calling thread."""
    server = make_server(Path(queue_path), host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
