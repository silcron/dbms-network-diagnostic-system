from __future__ import annotations

import csv
import io
import json
import math
import re
from typing import Dict, Final, List, NamedTuple, NoReturn, Tuple, Union


MAX_MESSAGE_BYTES: Final = 1_048_576
MAX_DECODED_STREAM_BYTES: Final = 8 * 1024 * 1024

JsonPrimitive = Union[None, bool, int, float, str]
JsonValue = Union[JsonPrimitive, List["JsonValue"], Dict[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]

TSHARK_FIELDS: Final = tuple(
    (
        "frame.number tcp.stream ip.src ipv6.src tcp.srcport ip.dst ipv6.dst "
        "tcp.dstport tcp.flags.syn tcp.flags.ack tcp.flags.reset tcp.seq_raw tcp.ack_raw "
        "tcp.len tcp.analysis.retransmission tcp.analysis.fast_retransmission "
        "tcp.analysis.out_of_order tcp.analysis.lost_segment "
        "tcp.segment.overlap.conflict mysql.version tls.record tls.handshake.type "
        "tls.handshake.extensions.supported_version"
    ).split()
)


class PacketDataError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


class Endpoint(NamedTuple):
    address: str
    port: int


class TransportFlags(NamedTuple):
    retransmission: bool
    fast_retransmission: bool
    out_of_order: bool
    lost_segment: bool


class TcpPacket(NamedTuple):
    frame: int
    stream: int
    source: Endpoint
    destination: Endpoint
    syn: bool
    ack: bool
    reset: bool
    sequence: int
    acknowledgment: int
    payload_length: int
    transport: TransportFlags
    overlap_conflict: bool
    mysql_version: str
    tls_record: bool
    tls_handshake_type: str
    tls_selected_version: str


class FollowStream(NamedTuple):
    stream: int
    node0: Endpoint
    node1: Endpoint
    node0_bytes: bytes
    node1_bytes: bytes


def _integer(value: str, field: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise PacketDataError("invalid_field", f"{field} is not an integer") from exc


def _boolean(value: str, field: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise PacketDataError("invalid_field", f"{field} is not a boolean")


def _optional_boolean(value: str, field: str) -> bool:
    return False if value == "" else _boolean(value, field)


def _address(ipv4: str, ipv6: str, field: str) -> str:
    if bool(ipv4) == bool(ipv6):
        raise PacketDataError("invalid_field", f"{field} address is missing or conflicting")
    return ipv4 or ipv6


def parse_tshark_fields(text: str) -> tuple[TcpPacket, ...]:
    rows = list(csv.reader(io.StringIO(text), delimiter="\t"))
    if not rows:
        raise PacketDataError("empty_fields", "TShark returned no field header")
    if tuple(rows[0]) != TSHARK_FIELDS:
        raise PacketDataError("field_header", "TShark field header was unexpected")

    packets: list[TcpPacket] = []
    for line_number, row in enumerate(rows[1:], start=2):
        if len(row) != len(TSHARK_FIELDS):
            raise PacketDataError(
                "field_columns", f"TShark field row {line_number} has {len(row)} columns"
            )
        packets.append(
            TcpPacket(
                frame=_integer(row[0], "frame.number"),
                stream=_integer(row[1], "tcp.stream"),
                source=Endpoint(
                    _address(row[2], row[3], "source"),
                    _integer(row[4], "tcp.srcport"),
                ),
                destination=Endpoint(
                    _address(row[5], row[6], "destination"),
                    _integer(row[7], "tcp.dstport"),
                ),
                syn=_boolean(row[8], "tcp.flags.syn"),
                ack=_boolean(row[9], "tcp.flags.ack"),
                reset=_boolean(row[10], "tcp.flags.reset"),
                sequence=_integer(row[11], "tcp.seq_raw"),
                acknowledgment=_integer(row[12], "tcp.ack_raw"),
                payload_length=_integer(row[13], "tcp.len"),
                transport=TransportFlags(
                    bool(row[14]), bool(row[15]), bool(row[16]), bool(row[17])
                ),
                overlap_conflict=_optional_boolean(
                    row[18], "tcp.segment.overlap.conflict"
                ),
                mysql_version=row[19],
                tls_record=bool(row[20]),
                tls_handshake_type=row[21],
                tls_selected_version=row[22],
            )
        )
    if not packets:
        raise PacketDataError("no_packets", "no TCP packets matched the configured ports")
    return tuple(packets)


def _follow_endpoint(line: str, label: str) -> Endpoint:
    value = line[len(label) :]
    try:
        address, port_text = value.rsplit(":", 1)
    except ValueError as exc:
        raise PacketDataError("follow_node", f"invalid {label.rstrip()} metadata") from exc
    port = _integer(port_text, f"{label.rstrip()} port")
    if not address or not 1 <= port <= 65_535:
        raise PacketDataError("follow_node", f"invalid {label.rstrip()} endpoint")
    return Endpoint(address.strip("[]"), port)


def parse_follow_raw(
    text: str,
    expected_stream: int,
    decoded_limit: int = MAX_DECODED_STREAM_BYTES,
) -> FollowStream:
    follow_seen = False
    filter_stream = -1
    nodes: list[Endpoint] = []
    chunks: tuple[list[bytes], list[bytes]] = ([], [])
    decoded_bytes = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        stripped = line.strip()
        if not stripped or set(stripped) == {"="}:
            continue
        if stripped == "Follow: tcp,raw":
            if follow_seen:
                raise PacketDataError("follow_conflict", "duplicate follow header")
            follow_seen = True
            continue
        if stripped.startswith("Filter: tcp.stream eq "):
            if filter_stream != -1:
                raise PacketDataError("follow_conflict", "duplicate follow filter")
            filter_stream = _integer(stripped.rsplit(" ", 1)[1], "follow stream")
            continue
        if stripped.startswith("Node 0: ") or stripped.startswith("Node 1: "):
            label = "Node 0: " if stripped.startswith("Node 0: ") else "Node 1: "
            expected_node = len(nodes)
            if label != f"Node {expected_node}: ":
                raise PacketDataError("follow_conflict", "follow nodes are duplicated or reordered")
            nodes.append(_follow_endpoint(stripped, label))
            continue

        direction = 1 if line.startswith("\t") else 0
        hex_text = stripped
        if len(nodes) != 2 or not re.fullmatch(r"[0-9A-Fa-f]+", hex_text):
            raise PacketDataError("follow_data", "invalid raw follow output")
        if len(hex_text) % 2:
            raise PacketDataError("follow_hex", "raw follow data has an odd hex length")
        decoded = bytes.fromhex(hex_text)
        decoded_bytes += len(decoded)
        if decoded_bytes > decoded_limit:
            raise PacketDataError(
                "decoded_limit", f"decoded stream data exceeded {decoded_limit} bytes"
            )
        chunks[direction].append(decoded)

    if not follow_seen or filter_stream != expected_stream or len(nodes) != 2:
        raise PacketDataError("follow_metadata", "raw follow metadata is incomplete")
    if nodes[0] == nodes[1]:
        raise PacketDataError("follow_conflict", "raw follow endpoints conflict")
    return FollowStream(
        expected_stream,
        nodes[0],
        nodes[1],
        b"".join(chunks[0]),
        b"".join(chunks[1]),
    )


def _reject_constants(value: str) -> NoReturn:
    raise PacketDataError("nonfinite_json", f"JSON constant {value} is not finite")


def _pairs_hook(pairs: List[Tuple[str, JsonValue]]) -> JsonObject:
    result: JsonObject = {}
    for key, value in pairs:
        if key in result:
            raise PacketDataError("duplicate_json_key", f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json_value(value: JsonValue) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise PacketDataError("nonfinite_json", "JSON number is not finite")
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        parsed: JsonObject = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise PacketDataError("json_key", "JSON object key is not text")
            parsed[key] = _json_value(child)
        return parsed
    raise PacketDataError("json_type", "JSON contains an unsupported value")


def decode_framed_messages(data: bytes) -> tuple[JsonObject, ...]:
    messages: list[JsonObject] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise PacketDataError("truncated_header", "stream ends inside a length header")
        length = int.from_bytes(data[offset : offset + 4], "big")
        offset += 4
        if length == 0 or length > MAX_MESSAGE_BYTES:
            raise PacketDataError("invalid_length", f"invalid protocol frame length {length}")
        if len(data) - offset < length:
            raise PacketDataError("truncated_body", "stream ends inside a JSON frame")
        payload = data[offset : offset + length]
        offset += length
        try:
            decoded: JsonValue = json.loads(
                payload.decode("utf-8"),
                parse_constant=_reject_constants,
                object_pairs_hook=_pairs_hook,
            )
            value = _json_value(decoded)
        except PacketDataError:
            raise
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise PacketDataError("invalid_json", "frame is not valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise PacketDataError("json_object", "protocol frame is not a JSON object")
        messages.append(value)
    return tuple(messages)
