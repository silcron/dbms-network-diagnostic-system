from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Optional, Type, Union
from urllib.parse import unquote, urlsplit

from scripts.fault_json import JsonBoundaryError, JsonObject, JsonValue, dumps_finite_json, loads_finite_json


LogArgument = Union[str, int, float]


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class RequestRecord:
    method: str
    path: str
    body: Optional[JsonObject]


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ContractState:
    proxies: dict[str, JsonObject] = field(default_factory=dict)
    requests: list[RequestRecord] = field(default_factory=list)
    invalid_create_response: bool = False
    raw_response: Optional[bytes] = None
    response_byte_delay_seconds: float = 0.0
    drop_responses_after_requests: Optional[int] = None


class ContractHandler(BaseHTTPRequestHandler):
    server: "ContractHttpServer"

    def _read_body(self) -> Optional[JsonObject]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return None
        try:
            value = loads_finite_json(self.rfile.read(length))
        except JsonBoundaryError as exc:
            raise InvalidContractRequest("test request body must be finite JSON") from exc
        if not isinstance(value, dict):
            raise InvalidContractRequest("test request body must be an object")
        return value

    def _record(self, body: Optional[JsonObject]) -> str:
        path = urlsplit(self.path).path
        self.server.state.requests.append(RequestRecord(self.command, path, body))
        return path

    def _send(self, status: int, body: Optional[JsonObject] = None) -> None:
        payload = (
            self.server.state.raw_response
            if self.server.state.raw_response is not None
            else b"" if body is None else dumps_finite_json(body).encode("utf-8")
        )
        self.send_response(status)
        if payload:
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            try:
                if self.server.state.response_byte_delay_seconds > 0:
                    for byte in payload:
                        self.wfile.write(bytes((byte,)))
                        self.wfile.flush()
                        time.sleep(self.server.state.response_byte_delay_seconds)
                else:
                    self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _drop_response(self) -> bool:
        limit = self.server.state.drop_responses_after_requests
        if limit is None or len(self.server.state.requests) <= limit:
            return False
        self.close_connection = True
        self.connection.close()
        return True

    def do_GET(self) -> None:
        path = self._record(None)
        if self._drop_response():
            return
        if path == "/proxies":
            self._send(200, self.server.state.proxies)
            return
        self._send(404, {"error": "missing"})

    def do_POST(self) -> None:
        body = self._read_body()
        path = self._record(body)
        if self._drop_response():
            return
        if path == "/proxies" and body is not None:
            name = str(body["name"])
            if name in self.server.state.proxies:
                self._send(409, {"error": "proxy already exists"})
                return
            proxy = {
                "name": name,
                "listen": body["listen"],
                "upstream": body["upstream"],
                "enabled": True,
                "toxics": [],
            }
            self.server.state.proxies[name] = proxy
            response = {**proxy, "name": "wrong-name"} if self.server.state.invalid_create_response else proxy
            self._send(201, response)
            return

        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "proxies" and parts[2] == "toxics" and body is not None:
            name = unquote(parts[1])
            proxy = self.server.state.proxies.get(name)
            if proxy is None:
                self._send(404, {"error": "proxy missing"})
                return
            toxic: JsonObject = dict(body)
            toxics = proxy.get("toxics")
            if not isinstance(toxics, list):
                raise InvalidContractRequest("test proxy toxics must be a list")
            toxics.append(toxic)
            self._send(200, toxic)
            return
        self._send(404, {"error": "missing"})

    def do_DELETE(self) -> None:
        path = self._record(None)
        if self._drop_response():
            return
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "proxies":
            name = unquote(parts[1])
            if self.server.state.proxies.pop(name, None) is None:
                self._send(404, {"error": "proxy missing"})
                return
            self._send(204)
            return
        if len(parts) == 4 and parts[0] == "proxies" and parts[2] == "toxics":
            name, toxic_name = unquote(parts[1]), unquote(parts[3])
            proxy = self.server.state.proxies.get(name)
            if proxy is None:
                self._send(404, {"error": "proxy missing"})
                return
            toxics = proxy["toxics"]
            if not isinstance(toxics, list):
                raise InvalidContractRequest("test proxy toxics must be a list")
            proxy["toxics"] = [item for item in toxics if item["name"] != toxic_name]
            self._send(204)
            return
        self._send(404, {"error": "missing"})

    def log_message(self, format: str, *args: LogArgument) -> None:
        return


class ContractHttpServer(ThreadingHTTPServer):
    state: ContractState


class InvalidContractRequest(TypeError):
    pass


class RunningContractServer:
    def __init__(self, state: Optional[ContractState] = None) -> None:
        self.state = state or ContractState()
        self.server = ContractHttpServer(("127.0.0.1", 0), ContractHandler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> "RunningContractServer":
        self.thread.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
