"""Small local web client for demonstrating the TCP diagnostic system in a browser."""

from __future__ import annotations

import argparse
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Final

from .client_core import DiagnosticClient
from .metrics import InvalidMetricValue, WebMetrics
from .protocol import ProtocolError
from .web_http import encode_json_object, parse_diagnostic_observation
from .web_inputs import DiagnosticRequest, HttpInputError, JsonObject, parse_diagnostic_request

STATIC_DIR: Final = Path(__file__).with_name("static")
MAX_HTTP_BODY_BYTES: Final = 4096
HTTP_BODY_DEADLINE_SECONDS: Final = 2.0
_METRICS_INIT_LOCK: Final = Lock()


class DiagnosticTransportFailure(Exception):
    """Keep the observed TCP phase without exposing raw exception text to HTTP."""

    def __init__(self, phase: str, cause: OSError | ProtocolError) -> None:
        self.phase = phase
        self.cause = cause
        super().__init__(phase)


def _transport_reason(error: OSError | ProtocolError) -> str:
    if isinstance(error, socket.gaierror):
        return "name_resolution"
    if isinstance(error, ConnectionRefusedError):
        return "connection_refused"
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(error, ConnectionResetError):
        return "connection_reset"
    if isinstance(error, ProtocolError):
        return "protocol_error"
    return "network_error"


class DiagnosticWebServer(ThreadingHTTPServer):
    request_queue_size = 32

    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(server_address, handler)
        self.metrics = WebMetrics()


class WebClientHandler(BaseHTTPRequestHandler):
    server_version = "DBMSDiagnosticWebClient/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/style.css":
            self._send_file(STATIC_DIR / "style.css", "text/css; charset=utf-8")
            return
        if self.path == "/app.js":
            self._send_file(STATIC_DIR / "app.js", "application/javascript; charset=utf-8")
            return
        if self.path == "/metrics":
            self._send_metrics()
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/api/diagnose":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        metrics = self._metrics()
        try:
            request = parse_diagnostic_request(self._read_request_body())
        except HttpInputError as exc:
            self.close_connection = True
            metrics.observe_request(exc.mode, "invalid_request")
            self._send_failure(HTTPStatus.BAD_REQUEST, exc.code, exc.message)
            return

        try:
            response = self._diagnose(request)
            observation = parse_diagnostic_observation(response)
            if observation.mode != request.mode:
                raise ProtocolError("Diagnostic result mode does not match the request.")
            encoded_response = encode_json_object(response)
        except DiagnosticTransportFailure as exc:
            reason = _transport_reason(exc.cause)
            timeout = reason == "timeout"
            metrics.observe_request(request.mode, "upstream_timeout" if timeout else "upstream_failure")
            self._send_failure(
                HTTPStatus.GATEWAY_TIMEOUT if timeout else HTTPStatus.BAD_GATEWAY,
                "upstream_timeout" if timeout else "upstream_failure",
                "diagnostic server timed out" if timeout else "diagnostic server communication failed",
                reason_code=reason,
                phase=exc.phase,
                system_errno=exc.cause.errno if isinstance(exc.cause, OSError) else None,
            )
            return
        except (socket.timeout, TimeoutError):
            metrics.observe_request(request.mode, "upstream_timeout")
            self._send_failure(
                HTTPStatus.GATEWAY_TIMEOUT,
                "upstream_timeout",
                "diagnostic server timed out",
            )
            return
        except (ConnectionError, OSError, ProtocolError, InvalidMetricValue):
            metrics.observe_request(request.mode, "upstream_failure")
            self._send_failure(
                HTTPStatus.BAD_GATEWAY,
                "upstream_failure",
                "diagnostic server communication failed",
            )
            return
        except Exception:  # noqa: BROAD_EXCEPT_OK
            metrics.observe_request(request.mode, "internal_error")
            self._send_failure(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "internal diagnostic error",
            )
            return

        metrics.observe_request(request.mode, "success")
        metrics.observe_result(observation)
        self._send_json_bytes(HTTPStatus.OK, encoded_response)

    def _read_request_body(self) -> bytes:
        if self.headers.get("Transfer-Encoding") is not None:
            self._discard_request_body(MAX_HTTP_BODY_BYTES)
            raise HttpInputError("invalid_request", "transfer encoding is not supported")
        content_lengths = self.headers.get_all("Content-Length", [])
        if len(content_lengths) != 1:
            raise HttpInputError("invalid_request", "one Content-Length header is required")
        try:
            length = int(content_lengths[0], 10)
        except ValueError as exc:
            raise HttpInputError("invalid_request", "Content-Length must be an integer") from exc
        if length <= 0:
            raise HttpInputError("invalid_request", "request body must not be empty")
        if length > MAX_HTTP_BODY_BYTES:
            self._discard_request_body(length)
            raise HttpInputError(
                "invalid_request",
                f"request body must be at most {MAX_HTTP_BODY_BYTES} bytes",
            )

        remaining = length
        chunks: list[bytes] = []
        deadline = monotonic() + HTTP_BODY_DEADLINE_SECONDS
        original_timeout = self.connection.gettimeout()
        try:
            while remaining > 0:
                seconds_left = deadline - monotonic()
                if seconds_left <= 0:
                    raise HttpInputError("invalid_request", "request body deadline exceeded")
                self.connection.settimeout(seconds_left)
                chunk = self.rfile.read1(remaining)
                if not chunk:
                    raise HttpInputError("invalid_request", "request body was truncated")
                chunks.append(chunk)
                remaining -= len(chunk)
        except (socket.timeout, TimeoutError) as exc:
            raise HttpInputError("invalid_request", "request body deadline exceeded") from exc
        finally:
            self.connection.settimeout(original_timeout)
        return b"".join(chunks)

    def _discard_request_body(self, length: int) -> None:
        remaining = length
        deadline = monotonic() + HTTP_BODY_DEADLINE_SECONDS
        original_timeout = self.connection.gettimeout()
        try:
            while remaining > 0:
                seconds_left = deadline - monotonic()
                if seconds_left <= 0:
                    return
                self.connection.settimeout(seconds_left)
                chunk = self.rfile.read1(min(remaining, MAX_HTTP_BODY_BYTES))
                if not chunk:
                    return
                remaining -= len(chunk)
        except (ConnectionError, OSError, TimeoutError):
            return
        finally:
            self.connection.settimeout(original_timeout)

    @staticmethod
    def _diagnose(request: DiagnosticRequest) -> JsonObject:
        client = DiagnosticClient(request.host, request.port)
        try:
            try:
                hello: JsonObject = client.connect()
            except (OSError, ProtocolError) as exc:
                raise DiagnosticTransportFailure("connect", exc) from exc
            try:
                diagnosis: JsonObject = client.diagnose(request.demo_scenario, request.mode)
            except (OSError, ProtocolError) as exc:
                raise DiagnosticTransportFailure("diagnose", exc) from exc
            if diagnosis.get("type") != "diagnostic_result":
                raise ProtocolError("Diagnostic server returned an invalid response type.")
            session_message = hello.get("message")
            if isinstance(session_message, str):
                diagnosis["session_message"] = session_message
            return diagnosis
        finally:
            try:
                client.logout()
            except (ConnectionError, OSError, ProtocolError):
                client.close()

    def _metrics(self) -> WebMetrics:
        with _METRICS_INIT_LOCK:
            server = getattr(self, "server", None)
            owner = self if server is None else server
            metrics = getattr(owner, "metrics", None)
            if isinstance(metrics, WebMetrics):
                return metrics
            metrics = WebMetrics()
            setattr(owner, "metrics", metrics)
            return metrics

    def _send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_metrics(self) -> None:
        data = self._metrics().render().encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_failure(
        self, status: HTTPStatus, code: str, message: str, *,
        reason_code: str | None = None, phase: str | None = None, system_errno: int | None = None,
    ) -> None:
        payload: JsonObject = {"code": code, "message": message, "error": message}
        if reason_code is not None:
            payload["reason_code"] = reason_code
        if phase is not None:
            payload["phase"] = phase
        if system_errno is not None:
            payload["system_errno"] = system_errno
        self._send_json(status, payload)

    def _send_json(self, status: HTTPStatus, payload: JsonObject) -> None:
        self._send_json_bytes(status, encode_json_object(payload))

    def _send_json_bytes(self, status: HTTPStatus, data: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: OBJECT_OK
        print(f"[web-client] {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser client for the DBMS diagnostic TCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with DiagnosticWebServer((args.host, args.port), WebClientHandler) as server:
        print(f"Open http://{args.host}:{args.port} in a browser.")
        server.serve_forever()


if __name__ == "__main__":
    main()
