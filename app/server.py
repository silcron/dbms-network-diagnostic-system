"""Threaded TCP server for DBMS connection diagnostics."""

from __future__ import annotations

import argparse
import secrets
import socketserver
import time
from threading import Lock
from typing import Final

from .db_targets import diagnose_targets
from .diagnostics import build_osi_trace, utc_now
from .protocol import (
    DEFAULT_FRAME_TIMEOUT_SECONDS,
    DEFAULT_IDLE_TIMEOUT_SECONDS,
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PROTOCOL_VERSION,
    JsonObject,
    ProtocolError,
    recv_message,
    send_message,
)

VALID_MODES: Final = frozenset({"auto", "live", "demo"})
VALID_DEMO_SCENARIOS: Final = frozenset({"normal", "warning", "down", "mixed"})


class SessionStore:
    def __init__(self) -> None:
        self.values: set[str] = set()
        self.lock = Lock()

    def issue(self) -> str:
        token = secrets.token_urlsafe(18)
        with self.lock:
            self.values.add(token)
        return token

    def contains(self, token: str) -> bool:
        with self.lock:
            return token in self.values

    def discard(self, token: str) -> None:
        with self.lock:
            self.values.discard(token)


class DiagnosticServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], handler: type[socketserver.BaseRequestHandler]):
        super().__init__(server_address, handler)
        self.sessions = SessionStore()
        self.idle_timeout_seconds = DEFAULT_IDLE_TIMEOUT_SECONDS
        self.frame_timeout_seconds = DEFAULT_FRAME_TIMEOUT_SECONDS
        self.send_timeout_seconds = DEFAULT_SEND_TIMEOUT_SECONDS


class DiagnosticHandler(socketserver.BaseRequestHandler):
    server: DiagnosticServer
    session_id: str | None

    def handle(self) -> None:
        peer_ip, _ = self.client_address
        self.session_id = None
        try:
            while True:
                try:
                    request = recv_message(
                        self.request,
                        idle_timeout=self.server.idle_timeout_seconds,
                        frame_timeout=self.server.frame_timeout_seconds,
                    )
                except ProtocolError:
                    self.send_error("invalid_request", "The protocol frame is invalid.")
                    return
                response = self.dispatch(request, peer_ip)
                send_message(self.request, response, timeout=self.server.send_timeout_seconds)
                if response.get("type") == "logout_ok":
                    return
        except OSError:
            return
        except Exception:  # noqa: BROAD_EXCEPT_OK - request-handler boundary isolates server threads.
            self.send_error("internal_error", "The server could not process the request.")
        finally:
            if self.session_id is not None:
                self.server.sessions.discard(self.session_id)

    def send_error(self, code: str, message: str) -> None:
        try:
            send_message(
                self.request,
                {"type": "error", "code": code, "message": message},
                timeout=self.server.send_timeout_seconds,
            )
        except (OSError, ProtocolError):
            return

    @staticmethod
    def error_response(code: str, message: str) -> JsonObject:
        return {"type": "error", "code": code, "message": message}

    def dispatch(self, request: JsonObject, peer_ip: str) -> JsonObject:
        request_type = request.get("type")
        if not isinstance(request_type, str) or not request_type:
            return self.error_response("invalid_request", "Request type must be a non-empty string.")
        if request_type == "hello":
            if self.session_id is None:
                self.session_id = self.server.sessions.issue()
            return {
                "type": "hello_ok",
                "protocol_version": PROTOCOL_VERSION,
                "session_id": self.session_id,
                "server_time": utc_now(),
                "message": "Session established. You can request a DBMS diagnostic.",
            }

        session_id = request.get("session_id")
        if (
            not isinstance(session_id, str)
            or session_id != self.session_id
            or not self.server.sessions.contains(session_id)
        ):
            return self.error_response("invalid_session", "A valid session ID is required.")

        if request_type == "diagnose":
            mode = request.get("mode", "auto")
            if not isinstance(mode, str) or mode not in VALID_MODES:
                return self.error_response("invalid_mode", "Mode must be auto, live, or demo.")
            demo_scenario = request.get("demo_scenario", "normal")
            if not isinstance(demo_scenario, str) or demo_scenario not in VALID_DEMO_SCENARIOS:
                return self.error_response(
                    "invalid_scenario",
                    "Demo scenario must be normal, warning, down, or mixed.",
                )
            started = time.perf_counter()
            results = diagnose_targets(demo_scenario, mode)
            # Older clients read a single result. Surface the most severe target.
            result = max(results, key=lambda db: ("NORMAL", "WARNING", "DOWN").index(db["status"]))
            server_rtt = round((time.perf_counter() - started) * 1000, 2)
            server_ip, server_port = self.server.server_address
            return {
                "type": "diagnostic_result",
                "checked_at": utc_now(),
                "server_processing_ms": server_rtt,
                "result": result,
                "results": results,
                "osi_trace": build_osi_trace(peer_ip, str(server_ip), int(server_port), session_id),
            }

        if request_type == "logout":
            return {"type": "logout_ok", "message": "Session closed."}

        return self.error_response("unsupported_type", "The request type is not supported.")


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
