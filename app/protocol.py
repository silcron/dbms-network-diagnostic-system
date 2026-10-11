"""JSON-over-TCP protocol with a four-byte length header."""

from __future__ import annotations

import json
import math
import socket
import struct
import time
from typing import Final, Union

MAX_MESSAGE_BYTES: Final = 1_048_576
PROTOCOL_VERSION: Final = 2
DEFAULT_IDLE_TIMEOUT_SECONDS: Final = 60.0
DEFAULT_FRAME_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_SEND_TIMEOUT_SECONDS: Final = 5.0

JsonPrimitive = Union[None, bool, int, float, str]
JsonValue = Union[JsonPrimitive, list["JsonValue"], dict[str, "JsonValue"]]
JsonObject = dict[str, JsonValue]


class ProtocolError(ValueError):
    """Raised when a peer sends an invalid protocol message."""


def _deadline_after(timeout: float) -> float:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ProtocolError("Protocol timeouts must be finite and positive.")
    return time.monotonic() + timeout


def recv_exact(sock: socket.socket, size: int, deadline: float | None = None) -> bytes:
    """Read exactly *size* bytes or raise ConnectionError."""
    if size < 0:
        raise ProtocolError("Read size cannot be negative.")
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        if deadline is not None:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise TimeoutError("Protocol frame receive deadline exceeded.")
            sock.settimeout(seconds_left)
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("Peer closed the connection.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_message(message: JsonObject) -> bytes:
    try:
        payload = json.dumps(
            message,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except ProtocolError:
        raise
    except (TypeError, ValueError, RecursionError) as exc:
        raise ProtocolError(f"Message cannot be JSON encoded: {exc}") from exc
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the maximum protocol size.")
    return struct.pack("!I", len(payload)) + payload


def _reject_nonfinite(token: str) -> JsonValue:
    raise ProtocolError(f"JSON number {token!r} is not finite.")


def _parse_finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise ProtocolError(f"JSON number {token!r} is not finite.")
    return value


def _reject_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> JsonObject:
    message: JsonObject = {}
    for key, value in pairs:
        if key in message:
            raise ProtocolError(f"JSON object contains duplicate key {key!r}.")
        message[key] = value
    return message


def decode_payload(payload: bytes) -> JsonObject:
    try:
        message: JsonValue = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
            parse_float=_parse_finite_float,
        )
    except ProtocolError:
        raise
    except (ValueError, RecursionError) as exc:
        raise ProtocolError("Payload is not valid UTF-8 JSON.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Protocol message must be a JSON object.")
    return message


def send_message(
    sock: socket.socket,
    message: JsonObject,
    *,
    timeout: float = DEFAULT_SEND_TIMEOUT_SECONDS,
) -> None:
    frame = encode_message(message)
    deadline = _deadline_after(timeout)
    previous_timeout = sock.gettimeout()
    remaining = memoryview(frame)
    try:
        while remaining:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise TimeoutError("Protocol frame send deadline exceeded.")
            sock.settimeout(seconds_left)
            sent = sock.send(remaining)
            if sent == 0:
                raise ConnectionError("Peer closed the connection during send.")
            remaining = remaining[sent:]
    finally:
        sock.settimeout(previous_timeout)


def recv_message(
    sock: socket.socket,
    *,
    idle_timeout: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
    frame_timeout: float = DEFAULT_FRAME_TIMEOUT_SECONDS,
) -> JsonObject:
    previous_timeout = sock.gettimeout()
    try:
        sock.settimeout(idle_timeout)
        first_header_byte = recv_exact(sock, 1)
        frame_deadline = _deadline_after(frame_timeout)
        header = first_header_byte + recv_exact(sock, 3, frame_deadline)
        length = struct.unpack("!I", header)[0]
        if length == 0 or length > MAX_MESSAGE_BYTES:
            raise ProtocolError("Received an invalid message length.")
        return decode_payload(recv_exact(sock, length, frame_deadline))
    finally:
        sock.settimeout(previous_timeout)
