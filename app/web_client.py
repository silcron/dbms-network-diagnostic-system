"""Small local web client for demonstrating the TCP diagnostic system in a browser."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .client_core import DiagnosticClient
from .protocol import ProtocolError

STATIC_DIR = Path(__file__).with_name("static")
MAX_HTTP_BODY_BYTES = 4096


class WebClientHandler(BaseHTTPRequestHandler):
    server_version = "DBMSDiagnosticWebClient/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif self.path == "/style.css":
            self._send_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
        elif self.path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/diagnose":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_HTTP_BODY_BYTES:
                raise ValueError("request body must be between 1 and 4096 bytes")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            host = str(payload.get("host", "127.0.0.1"))
            port = int(payload.get("port", 9090))
            if not 1 <= port <= 65535:
                raise ValueError("port must be between 1 and 65535")
            mode = str(payload.get("mode", "live"))
            if mode not in {"live", "demo"}:
                raise ValueError("mode must be live or demo")
            demo_scenario = str(payload.get("demo_scenario", "normal"))
            response = self._diagnose(host, port, demo_scenario, mode)
            self._send_json(HTTPStatus.OK, response)
        except Exception as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": f"{type(exc).__name__}: {exc}"})

    @staticmethod
    def _diagnose(host: str, port: int, demo_scenario: str, mode: str) -> dict[str, Any]:
        """A browser request performs one complete TCP client session."""
        client = DiagnosticClient(host, port)
        try:
            hello = client.connect()
            diagnosis = client.diagnose(demo_scenario, mode)
            diagnosis["session_message"] = hello["message"]
            return diagnosis
        finally:
            try:
                client.logout()
            except (ConnectionError, OSError, ProtocolError):
                client.close()

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[web-client] {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser client for the DBMS diagnostic TCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with ThreadingHTTPServer((args.host, args.port), WebClientHandler) as server:
        print(f"Open http://{args.host}:{args.port} in a browser.")
        server.serve_forever()


if __name__ == "__main__":
    main()
