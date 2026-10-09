from __future__ import annotations

import ipaddress
from pathlib import Path
from typing import Final, NamedTuple, Optional

from scripts.packet_parsing import (
    MAX_DECODED_STREAM_BYTES,
    Endpoint,
    FollowStream,
    JsonObject,
    PacketDataError,
    TcpPacket,
    decode_framed_messages,
)
from scripts.packet_tshark import CaptureRequest, FollowQuery, PacketRunner


MAX_STREAMS: Final = 128


class Handshake(NamedTuple):
    stream: int
    service_port: int
    client: Endpoint
    server: Endpoint
    frames: tuple[int, int, int]


class StreamTransport(NamedTuple):
    stream: int
    retransmission: int
    fast_retransmission: int
    out_of_order: int
    lost_segment: int
    reset: int


class DiagnosticObservation(NamedTuple):
    stream: int
    direction: str
    message_index: int
    mode: str
    status: str
    osi_layers: int


class DiagnosticLocation(NamedTuple):
    stream: int
    direction: str
    message_index: int


class DatabaseEvidence(NamedTuple):
    streams: int
    greetings: tuple[str, ...]
    tls_records: int
    selected_versions: tuple[str, ...]


class CaptureReport(NamedTuple):
    capture: Path
    handshakes: tuple[Handshake, ...]
    transports: tuple[StreamTransport, ...]
    diagnostics: tuple[DiagnosticObservation, ...]
    database: DatabaseEvidence
    loopback_only: bool


def _validate_request(request: CaptureRequest) -> None:
    if not request.capture.is_file():
        raise PacketDataError(
            "capture_missing", f"capture file not found: {request.capture}"
        )
    if not 1 <= request.diagnostic_port <= 65_535:
        raise PacketDataError("diagnostic_port", "diagnostic port must be 1..65535")
    if not 1 <= request.db_port <= 65_535:
        raise PacketDataError("db_port", "database port must be 1..65535")
    if request.diagnostic_port == request.db_port:
        raise PacketDataError("port_conflict", "diagnostic and database ports must differ")


def _service_port(packets: list[TcpPacket], request: CaptureRequest) -> int:
    configured = {request.diagnostic_port, request.db_port}
    observed = {
        endpoint.port
        for packet in packets
        for endpoint in (packet.source, packet.destination)
        if endpoint.port in configured
    }
    if len(observed) != 1:
        raise PacketDataError("service_conflict", "TCP stream has conflicting service ports")
    return observed.pop()


def _handshake(packets: list[TcpPacket], service_port: int) -> Handshake:
    ordered = sorted(packets, key=lambda packet: packet.frame)
    syns: dict[frozenset[Endpoint], dict[tuple[Endpoint, Endpoint, int], TcpPacket]] = {}
    syn_acks: dict[frozenset[Endpoint], dict[
        tuple[Endpoint, Endpoint, int, int], tuple[TcpPacket, TcpPacket]
    ]] = {}
    for packet in ordered:
        connection = frozenset((packet.source, packet.destination))
        if packet.reset:
            syns.pop(connection, None)
            syn_acks.pop(connection, None)
            continue
        if packet.syn and not packet.ack and packet.destination.port == service_port:
            syn_key = (packet.destination, packet.source, (packet.sequence + 1) % (2**32))
            syns.setdefault(connection, {}).setdefault(syn_key, packet)
            continue
        if packet.syn and packet.ack:
            syn_key = (packet.source, packet.destination, packet.acknowledgment)
            syn = syns.get(connection, {}).get(syn_key)
            if syn is not None:
                ack_key = (
                    syn.source, syn.destination, (syn.sequence + 1) % (2**32),
                    (packet.sequence + 1) % (2**32),
                )
                syn_acks.setdefault(connection, {}).setdefault(ack_key, (syn, packet))
            continue
        if packet.syn or not packet.ack:
            continue
        pair = syn_acks.get(connection, {}).get(
            (packet.source, packet.destination, packet.sequence, packet.acknowledgment)
        )
        if pair is not None:
            syn, syn_ack = pair
            return Handshake(
                syn.stream, service_port, syn.source, syn.destination,
                (syn.frame, syn_ack.frame, packet.frame),
            )
    stream = ordered[0].stream
    raise PacketDataError("handshake", f"stream {stream} has no valid SYN/SYN-ACK/ACK sequence")


def _transport(packets: list[TcpPacket]) -> StreamTransport:
    conflict = next((packet for packet in packets if packet.overlap_conflict), None)
    if conflict is not None:
        raise PacketDataError(
            "overlap_conflict",
            f"stream {conflict.stream} frame {conflict.frame} has conflicting overlap data",
        )
    return StreamTransport(
        packets[0].stream,
        sum(packet.transport.retransmission for packet in packets),
        sum(packet.transport.fast_retransmission for packet in packets),
        sum(packet.transport.out_of_order for packet in packets),
        sum(packet.transport.lost_segment for packet in packets),
        sum(packet.reset for packet in packets),
    )


def _diagnostic_result(
    message: JsonObject,
    location: DiagnosticLocation,
) -> Optional[DiagnosticObservation]:
    if message.get("type") != "diagnostic_result":
        return None
    result = message.get("result")
    osi_trace = message.get("osi_trace")
    if not isinstance(result, dict) or not isinstance(osi_trace, list):
        raise PacketDataError("diagnostic_shape", "diagnostic result has invalid structure")
    mode = result.get("mode")
    status = result.get("status")
    if not isinstance(mode, str) or not isinstance(status, str):
        raise PacketDataError("diagnostic_shape", "diagnostic mode or status is invalid")
    if len(osi_trace) != 7 or not all(isinstance(layer, dict) for layer in osi_trace):
        raise PacketDataError("osi_trace", "diagnostic result does not contain seven OSI layers")
    return DiagnosticObservation(
        location.stream,
        location.direction,
        location.message_index,
        mode,
        status,
        7,
    )


def _diagnostics(
    followed: FollowStream, handshake: Handshake
) -> tuple[tuple[DiagnosticObservation, ...], int]:
    if {followed.node0, followed.node1} != {handshake.client, handshake.server}:
        raise PacketDataError("follow_conflict", "follow endpoints conflict with handshake")
    observations: list[DiagnosticObservation] = []
    total_bytes = len(followed.node0_bytes) + len(followed.node1_bytes)
    total_messages = 0
    for source, data in (
        (followed.node0, followed.node0_bytes),
        (followed.node1, followed.node1_bytes),
    ):
        messages = decode_framed_messages(data)
        total_messages += len(messages)
        direction = "client_to_server" if source == handshake.client else "server_to_client"
        for index, message in enumerate(messages, start=1):
            location = DiagnosticLocation(followed.stream, direction, index)
            observation = _diagnostic_result(message, location)
            if observation is not None:
                observations.append(observation)
    if total_messages == 0:
        raise PacketDataError("no_messages", f"stream {followed.stream} has no protocol messages")
    return tuple(observations), total_bytes


def _database_evidence(
    streams: list[list[TcpPacket]], db_port: int
) -> DatabaseEvidence:
    greetings: list[str] = []
    selected_versions: list[str] = []
    tls_records = 0
    for packets in streams:
        for packet in packets:
            if packet.mysql_version and packet.mysql_version not in greetings:
                greetings.append(packet.mysql_version)
            if packet.tls_record:
                tls_records += 1
            if (
                packet.source.port == db_port
                and packet.tls_handshake_type == "2"
                and packet.tls_selected_version
                and packet.tls_selected_version not in selected_versions
            ):
                selected_versions.append(packet.tls_selected_version)
    return DatabaseEvidence(
        len(streams), tuple(greetings), tls_records, tuple(selected_versions)
    )


def _is_loopback(endpoint: Endpoint) -> bool:
    address = endpoint.address.split("%", 1)[0]
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError as exc:
        raise PacketDataError("endpoint_address", "endpoint address is invalid") from exc


def analyze_capture(request: CaptureRequest, runner: PacketRunner) -> CaptureReport:
    _validate_request(request)
    packets = runner.packets(request)
    grouped: dict[int, list[TcpPacket]] = {}
    for packet in packets:
        grouped.setdefault(packet.stream, []).append(packet)
    if len(grouped) > MAX_STREAMS:
        raise PacketDataError(
            "stream_limit", f"capture contains more than {MAX_STREAMS} matching TCP streams"
        )

    handshakes: list[Handshake] = []
    transports: list[StreamTransport] = []
    diagnostics: list[DiagnosticObservation] = []
    db_streams: list[list[TcpPacket]] = []
    decoded_bytes = 0
    for stream, stream_packets in sorted(grouped.items()):
        service_port = _service_port(stream_packets, request)
        handshake = _handshake(stream_packets, service_port)
        handshakes.append(handshake)
        transports.append(_transport(stream_packets))
        if service_port == request.db_port:
            db_streams.append(stream_packets)
            continue
        followed = runner.follow(
            FollowQuery(
                request.capture,
                stream,
                MAX_DECODED_STREAM_BYTES - decoded_bytes,
            )
        )
        stream_diagnostics, stream_bytes = _diagnostics(followed, handshake)
        decoded_bytes += stream_bytes
        diagnostics.extend(stream_diagnostics)

    if not diagnostics:
        raise PacketDataError("no_diagnostic", "capture has no diagnostic_result message")
    loopback_only = all(
        _is_loopback(endpoint)
        for handshake in handshakes
        for endpoint in (handshake.client, handshake.server)
    )
    return CaptureReport(
        request.capture,
        tuple(handshakes),
        tuple(transports),
        tuple(diagnostics),
        _database_evidence(db_streams, request.db_port),
        loopback_only,
    )
