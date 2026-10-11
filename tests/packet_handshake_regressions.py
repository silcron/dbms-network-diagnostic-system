from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import NoReturn

from scripts.packet_analysis import _handshake, _transport, analyze_capture
from scripts.packet_parsing import (
    Endpoint, PacketDataError, TcpPacket, TransportFlags,
)
from scripts.packet_tshark import CaptureRequest, FollowQuery


class _AdversarialRunner:
    def __init__(self, packets: tuple[TcpPacket, ...]) -> None:
        self._packets = packets

    def packets(self, request: CaptureRequest) -> tuple[TcpPacket, ...]:
        return self._packets

    def follow(self, query: FollowQuery) -> NoReturn:
        raise AssertionError(f"unexpected follow call for stream {query.stream}")


def _packet(
    frame: int,
    source: Endpoint,
    destination: Endpoint,
    syn: bool,
    ack: bool,
    sequence: int,
    acknowledgment: int,
) -> TcpPacket:
    return TcpPacket(
        frame, 0, source, destination, syn, ack, False,
        sequence, acknowledgment, 0,
        TransportFlags(False, False, False, False),
        False, "", False, "", "",
    )


class HandshakeComplexityTests(unittest.TestCase):
    def test_same_direction_syn_ack_cannot_match_syn(self) -> None:
        # Given: SYN/ACK endpoints incorrectly match the initial SYN direction.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        packets = [
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, client, server, True, True, 2_000, 1_001),
            _packet(3, client, server, False, True, 1_001, 2_001),
        ]

        # When/Then: matching sequence numbers cannot replace endpoint direction.
        with self.assertRaises(PacketDataError) as raised:
            _handshake(packets, 9_090)
        self.assertEqual(raised.exception.code, "handshake")

    def test_reversed_final_ack_cannot_complete_handshake(self) -> None:
        # Given: valid SYN legs followed by an ACK in the server-to-client direction.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        packets = [
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, server, client, True, True, 2_000, 1_001),
            _packet(3, server, client, False, True, 1_001, 2_001),
        ]

        # When/Then: the final ACK must follow the original client-to-server direction.
        with self.assertRaises(PacketDataError) as raised:
            _handshake(packets, 9_090)
        self.assertEqual(raised.exception.code, "handshake")

    def test_interleaved_reset_invalidates_pending_handshake(self) -> None:
        # Given: a reset interrupts a SYN/SYN-ACK exchange before its final ACK.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        packets = (
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, server, client, True, True, 2_000, 1_001),
            _packet(3, server, client, False, True, 2_001, 1_001)._replace(reset=True),
            _packet(4, client, server, False, True, 1_001, 2_001),
        )

        # When/Then: the later ACK cannot revive the reset attempt.
        with self.assertRaises(PacketDataError) as raised:
            _handshake(list(packets), 9_090)
        self.assertEqual(raised.exception.code, "handshake")

    def test_fresh_handshake_after_reset_can_complete(self) -> None:
        # Given: a reset attempt followed by a fresh three-way handshake.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        packets = (
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, server, client, True, True, 2_000, 1_001),
            _packet(3, client, server, False, True, 1_001, 2_001)._replace(reset=True),
            _packet(4, client, server, True, False, 1_000, 0),
            _packet(5, server, client, True, True, 2_000, 1_001),
            _packet(6, client, server, False, True, 1_001, 2_001),
        )

        # When: handshake discovery scans the whole stream.
        handshake = _handshake(list(packets), 9_090)

        # Then: only the post-reset attempt is accepted.
        self.assertEqual(handshake.frames, (4, 5, 6))

    def test_reset_after_completed_handshake_is_transport_evidence(self) -> None:
        # Given: a complete handshake followed by a later reset.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        packets = [
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, server, client, True, True, 2_000, 1_001),
            _packet(3, client, server, False, True, 1_001, 2_001),
            _packet(4, server, client, False, True, 2_001, 1_001)._replace(reset=True),
        ]

        # When: handshake and transport evidence are derived.
        handshake = _handshake(packets, 9_090)
        transport = _transport(packets)

        # Then: the established connection remains valid and reports the reset.
        self.assertEqual(handshake.frames, (1, 2, 3))
        self.assertEqual(transport.reset, 1)

    def test_reset_packet_cannot_form_handshake(self) -> None:
        # Given: an otherwise valid handshake with reset on each possible leg.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        clean = (
            _packet(1, client, server, True, False, 1_000, 0),
            _packet(2, server, client, True, True, 2_000, 1_001),
            _packet(3, client, server, False, True, 1_001, 2_001),
        )

        for reset_index in range(3):
            with self.subTest(reset_index=reset_index):
                packets = [
                    packet._replace(reset=index == reset_index)
                    for index, packet in enumerate(clean)
                ]

                # When/Then: reset evidence cannot complete a three-way handshake.
                with self.assertRaises(PacketDataError):
                    _handshake(packets, 9_090)

    def test_invalid_handshake_rows_finish_within_runtime_bound(self) -> None:
        # Given: many matching SYN/SYN-ACK pairs whose final ACKs all conflict.
        count = 200
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        syns = tuple(
            _packet(index + 1, client, server, True, False, 1_000, 0)
            for index in range(count)
        )
        syn_acks = tuple(
            _packet(count + index + 1, server, client, True, True, 2_000, 1_001)
            for index in range(count)
        )
        invalid_acks = tuple(
            _packet(2 * count + index + 1, client, server, False, True, 1_001, 9_999)
            for index in range(count)
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = Path(temporary_directory) / "bounded.pcap"
            capture.write_bytes(b"fixture")
            request = CaptureRequest(capture, 9_090, 3_307)

            # When: public analysis rejects the adversarial stream.
            started = time.monotonic()
            with self.assertRaises(PacketDataError) as raised:
                analyze_capture(request, _AdversarialRunner(syns + syn_acks + invalid_acks))
            elapsed = time.monotonic() - started

        # Then: invalid sequence evidence fails promptly without a cubic scan.
        self.assertEqual(raised.exception.code, "handshake")
        self.assertLess(elapsed, 0.5)
