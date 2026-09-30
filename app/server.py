"""Threaded TCP server for DBMS connection diagnostics."""

from __future__ import annotations

import argparse
import secrets
import socketserver
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .diagnostics import build_osi_trace, local_ipv4, probe_database, utc_now
from .protocol import PROTOCOL_VERSION, ProtocolError, recv_message, send_message


@dataclass
class SessionStore:
    values: set[str] = field(default_factory=set)
    lock: Lock = field(default_factory=Lock)

    def issue(self) -> str:
        token = secrets.token_urlsafe(18)
        with self.lock:
            self.values.add(token)
        return token

    def contains(self, token: object) -> bool:
        if not isinstance(token, str):
            return False
        with self.lock:
            return token in self.values

    def discard(self, token: object) -> None:
        if isinstance(token, str):
            with self.lock:
                self.values.discard(token)


class DiagnosticServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler: type[socketserver.BaseRequestHandler]):
        super().__init__(server_address, handler)
        self.sessions = SessionStore()


class DiagnosticHandler(socketserver.BaseRequestHandler):
    server: DiagnosticServer

    def handle(self) -> None:
        peer_ip, _ = self.client_address
        while True:
            try:
                request = recv_message(self.request)
                response = self.dispatch(request, peer_ip)
                send_message(self.request, response)
            except ConnectionError:
                return
            except ProtocolError as exc:
                send_message(self.request, {"type": "error", "message": str(exc)})
                return
            except Exception as exc:
                send_message(self.request, {"type": "error", "message": f"Server error: {type(exc).__name__}"})
                return

    def dispatch(self, request: dict[str, Any], peer_ip: str) -> dict[str, Any]:
        request_type = request.get("type")
        if request_type == "hello":
            session_id = self.server.sessions.issue()
            return {
                "type": "hello_ok",
                "protocol_version": PROTOCOL_VERSION,
                "session_id": session_id,
                "server_time": utc_now(),
                "message": "Session established. You can request a DBMS diagnostic.",
            }

        session_id = request.get("session_id")
        if not self.server.sessions.contains(session_id):
            return {"type": "error", "message": "A valid session ID is required."}

        if request_type == "diagnose":
            mode = request.get("mode", "auto")
            if mode not in {"auto", "live", "demo"}:
                return {"type": "error", "message": "Mode must be auto, live, or demo."}
            started = time.perf_counter()
            result = probe_database(str(request.get("demo_scenario", "normal")), mode)
            server_rtt = round((time.perf_counter() - started) * 1000, 2)
            server_ip, server_port = self.server.server_address
            return {
                "type": "diagnostic_result",
                "checked_at": utc_now(),
                "server_processing_ms": server_rtt,
                "result": result,
                "osi_trace": build_osi_trace(peer_ip, str(server_ip), int(server_port), session_id),
            }

        if request_type == "logout":
            self.server.sessions.discard(session_id)
            return {"type": "logout_ok", "message": "Session closed."}

        return {"type": "error", "message": f"Unsupported request type: {request_type!r}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="DBMS diagnostic TCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=9090, type=int)
    args = parser.parse_args()
    with DiagnosticServer((args.host, args.port), DiagnosticHandler) as server:
        print(f"DBMS diagnostic server listening on {args.host}:{args.port}")
        print("Press Ctrl+C to stop.")
        server.serve_forever()


if __name__ == "__main__":
    main()
