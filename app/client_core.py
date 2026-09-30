"""Reusable synchronous client used by GUI and tests."""

from __future__ import annotations

import socket
import time
from typing import Any

from .protocol import recv_message, send_message


class DiagnosticClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9090, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.session_id: str | None = None

    def connect(self) -> dict[str, Any]:
        self.close()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            send_message(self.sock, {"type": "hello"})
            response = recv_message(self.sock)
            if response.get("type") != "hello_ok":
                raise ConnectionError(response.get("message", "Server rejected the connection."))
            self.session_id = str(response["session_id"])
            return response
        except Exception:
            self.close()
            raise

    def diagnose(self, demo_scenario: str = "normal", mode: str = "auto") -> dict[str, Any]:
        if not self.sock or not self.session_id:
            raise ConnectionError("Connect before requesting a diagnostic.")
        started = time.perf_counter()
        send_message(
            self.sock,
            {"type": "diagnose", "session_id": self.session_id, "demo_scenario": demo_scenario, "mode": mode},
        )
        response = recv_message(self.sock)
        response["client_rtt_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return response

    def logout(self) -> dict[str, Any] | None:
        if not self.sock or not self.session_id:
            return None
        try:
            send_message(self.sock, {"type": "logout", "session_id": self.session_id})
            return recv_message(self.sock)
        finally:
            self.close()

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None
                self.session_id = None
