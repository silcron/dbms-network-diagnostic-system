from __future__ import annotations

import json
import socket
import struct
from pathlib import Path
from typing import NamedTuple

from scripts.packet_parsing import JsonObject


class PacketSpec(NamedTuple):
    source_port: int
    destination_port: int
    sequence: int
    acknowledgment: int
    flags: int
    payload: bytes
    identifier: int


def encode_frame(message: JsonObject) -> bytes:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    return len(payload).to_bytes(4, "big") + payload


def _raw_packet(spec: PacketSpec) -> bytes:
    source = socket.inet_aton("127.0.0.1")
    destination = socket.inet_aton("127.0.0.1")
    total_length = 40 + len(spec.payload)
    ip_header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        total_length,
        spec.identifier,
        0,
        64,
        socket.IPPROTO_TCP,
        0,
        source,
        destination,
    )
    tcp_header = struct.pack(
        "!HHLLBBHHH",
        spec.source_port,
        spec.destination_port,
        spec.sequence,
        spec.acknowledgment,
        5 << 4,
        spec.flags,
        65_535,
        0,
        0,
    )
    return ip_header + tcp_header + spec.payload


def write_fragmented_capture(path: Path) -> None:
    client_port = 50_000
    server_port = 9_090
    client_sequence = 1_001
    server_sequence = 2_001
    client_data = encode_frame({"type": "hello"}) + encode_frame(
        {"type": "diagnose", "mode": "demo", "demo_scenario": "normal"}
    )
    server_data = encode_frame({"type": "hello_ok", "protocol_version": 2}) + encode_frame(
        {
            "type": "diagnostic_result",
            "result": {"mode": "DEMO", "status": "NORMAL"},
            "osi_trace": [{"layer": str(index)} for index in range(1, 8)],
        }
    )
    specs = (
        PacketSpec(client_port, server_port, 1_000, 0, 0x02, b"", 1),
        PacketSpec(server_port, client_port, 2_000, 1_001, 0x12, b"", 2),
        PacketSpec(client_port, server_port, 1_001, 2_001, 0x10, b"", 3),
        PacketSpec(
            client_port,
            server_port,
            client_sequence,
            server_sequence,
            0x18,
            client_data[:2],
            4,
        ),
        PacketSpec(
            client_port,
            server_port,
            client_sequence + 2,
            server_sequence,
            0x18,
            client_data[2:],
            5,
        ),
        PacketSpec(
            server_port,
            client_port,
            server_sequence,
            client_sequence + len(client_data),
            0x18,
            server_data,
            6,
        ),
    )
    content = bytearray(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65_535, 101))
    for index, spec in enumerate(specs):
        packet = _raw_packet(spec)
        content.extend(struct.pack("<IIII", 1_700_000_000, index * 1_000, len(packet), len(packet)))
        content.extend(packet)
    path.write_bytes(content)
