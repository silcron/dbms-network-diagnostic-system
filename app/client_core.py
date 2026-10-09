"""Reusable synchronous client used by GUI and tests."""

from __future__ import annotations

import math
import os
import socket
import time
from typing import Final

from .protocol import (
    DEFAULT_FRAME_TIMEOUT_SECONDS,
    DEFAULT_SEND_TIMEOUT_SECONDS,
    PROTOCOL_VERSION,
    JsonObject,
    ProtocolError,
    recv_message,
    send_message,
)

CLIENT_TIMEOUT_ENV: Final = "DIAGNOSTIC_TIMEOUT_SECONDS"
DEFAULT_CLIENT_TIMEOUT_SECONDS: Final = 10.0
MAX_CONFIGURED_CLIENT_TIMEOUT_SECONDS: Final = 120.0


class ClientConfigurationError(ValueError):
    name: str
    reason: str

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self.reason = reason
        super().__init__(f"{name} {reason}")


def _resolve_timeout(explicit: float | None) -> float:
    if explicit is not None:
        if not math.isfinite(explicit) or explicit <= 0:
            raise ClientConfigurationError("timeout", "must be finite and positive.")
        return float(explicit)

    configured = os.getenv(CLIENT_TIMEOUT_ENV, str(DEFAULT_CLIENT_TIMEOUT_SECONDS))
    try:
        timeout = float(configured)
    except ValueError as exc:
        raise ClientConfigurationError(
            CLIENT_TIMEOUT_ENV,
            "must be a finite positive number no greater than 120 seconds.",
        ) from exc
    if not math.isfinite(timeout) or not 0 < timeout <= MAX_CONFIGURED_CLIENT_TIMEOUT_SECONDS:
        raise ClientConfigurationError(
            CLIENT_TIMEOUT_ENV,
            "must be a finite positive number no greater than 120 seconds.",
        )
    return timeout


class DiagnosticClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9090, timeout: float | None = None):
        self.host = host
        self.port = port
        self.timeout = _resolve_timeout(timeout)
        self.sock: socket.socket | None = None
        self.session_id: str | None = None

    def connect(self) -> JsonObject:
        self.close()
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        connected = False
        try:
            sock.settimeout(self.timeout)
            send_message(sock, {"type": "hello"}, timeout=min(self.timeout, DEFAULT_SEND_TIMEOUT_SECONDS))
            response = recv_message(
                sock,
                idle_timeout=self.timeout,
                frame_timeout=min(self.timeout, DEFAULT_FRAME_TIMEOUT_SECONDS),
            )
            if response.get("type") != "hello_ok":
                raise ProtocolError("Server rejected the protocol handshake.")
            if response.get("protocol_version") != PROTOCOL_VERSION:
                raise ProtocolError("Server returned an unsupported protocol version.")
            session_id = response.get("session_id")
            if not isinstance(session_id, str) or not session_id.strip():
                raise ProtocolError("Server returned an invalid session token.")
            self.sock = sock
            self.session_id = session_id
            connected = True
            return response
        finally:
            if not connected:
                sock.close()

    def diagnose(self, demo_scenario: str = "normal", mode: str = "auto") -> JsonObject:
        if not self.sock or not self.session_id:
            raise ConnectionError("Connect before requesting a diagnostic.")
        started = time.perf_counter()
        try:
            send_message(
                self.sock,
                {"type": "diagnose", "session_id": self.session_id, "demo_scenario": demo_scenario, "mode": mode},
                timeout=min(self.timeout, DEFAULT_SEND_TIMEOUT_SECONDS),
            )
            response = recv_message(
                self.sock,
                idle_timeout=self.timeout,
                frame_timeout=min(self.timeout, DEFAULT_FRAME_TIMEOUT_SECONDS),
            )
            response_type = response.get("type")
            if not isinstance(response_type, str) or response_type not in {"diagnostic_result", "error"}:
                raise ProtocolError("Server returned an invalid diagnostic response.")
        except (OSError, ProtocolError):
            self.close()
            raise
        response["client_rtt_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return response

    def logout(self) -> JsonObject | None:
        if not self.sock or not self.session_id:
            return None
        try:
            send_message(
                self.sock,
                {"type": "logout", "session_id": self.session_id},
                timeout=min(self.timeout, DEFAULT_SEND_TIMEOUT_SECONDS),
            )
            return recv_message(
                self.sock,
                idle_timeout=self.timeout,
                frame_timeout=min(self.timeout, DEFAULT_FRAME_TIMEOUT_SECONDS),
            )
        finally:
            self.close()

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None
                self.session_id = None
