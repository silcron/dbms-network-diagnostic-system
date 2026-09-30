"""JSON-over-TCP protocol with a four-byte length header."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any

MAX_MESSAGE_BYTES = 1_048_576
PROTOCOL_VERSION = 2


class ProtocolError(ValueError):
    """Raised when a peer sends an invalid protocol message."""


def recv_exact(sock: socket.socket, size: int) -> bytes:
    """Read exactly *size* bytes or raise ConnectionError."""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("Peer closed the connection.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_message(message: dict[str, Any]) -> bytes:
    try:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"Message cannot be JSON encoded: {exc}") from exc
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Message exceeds the maximum protocol size.")
    return struct.pack("!I", len(payload)) + payload


def decode_payload(payload: bytes) -> dict[str, Any]:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Payload is not valid UTF-8 JSON.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Protocol message must be a JSON object.")
    return message


def send_message(sock: socket.socket, message: dict[str, Any]) -> None:
    sock.sendall(encode_message(message))


def recv_message(sock: socket.socket) -> dict[str, Any]:
    header = recv_exact(sock, 4)
    length = struct.unpack("!I", header)[0]
    if length == 0 or length > MAX_MESSAGE_BYTES:
        raise ProtocolError("Received an invalid message length.")
    return decode_payload(recv_exact(sock, length))
