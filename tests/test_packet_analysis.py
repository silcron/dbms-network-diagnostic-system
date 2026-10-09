from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import NoReturn, get_type_hints
from unittest.mock import patch

from packet_capture_fixture import encode_frame, write_fragmented_capture
from packet_handshake_regressions import HandshakeComplexityTests as HandshakeComplexityTests
from scripts.packet_analysis import CaptureReport, analyze_capture
from scripts.packet_parsing import (
    TSHARK_FIELDS, Endpoint, PacketDataError, TcpPacket, TransportFlags,
    decode_framed_messages, parse_follow_raw, parse_tshark_fields,
)
from scripts.packet_process import BoundedCommandError, CommandLimits, run_bounded
from scripts.packet_tshark import CaptureRequest, FollowQuery


ROOT = Path(__file__).resolve().parents[1]
TSHARK = shutil.which("tshark") or r"C:\Program Files\Wireshark\tshark.exe"


@unittest.skipUnless(Path(TSHARK).is_file(), "TShark is required for packet CLI tests")
class PacketAnalysisCliTests(unittest.TestCase):
    def _run_general(self, capture: str, tshark: str = TSHARK) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "scripts.check_packet_evidence",
                "--capture",
                capture,
                "--diagnostic-port",
                "9090",
                "--tshark",
                tshark,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )

    def test_general_capture_cli_reports_demo_diagnostic(self) -> None:
        # Given: the committed DEMO diagnostic capture and real TShark.
        capture = "demo_tcp_9090_20261005.pcapng"

        # When: general packet analysis is requested through the CLI.
        completed = self._run_general(capture)

        # Then: the CLI exposes the captured application result and OSI count.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("mode=DEMO status=NORMAL osi_layers=7", completed.stdout)

    def test_general_capture_cli_reports_live_tls_sql_opacity(self) -> None:
        # Given: the committed LIVE diagnostic/MariaDB capture and real TShark.
        capture = "live_mariadb_3307_20261005.pcapng"

        # When: general packet analysis is requested through the CLI.
        completed = self._run_general(capture)

        # Then: TLS evidence is explicit and encrypted SQL remains opaque.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("mode=LIVE status=NORMAL osi_layers=7", completed.stdout)
        self.assertIn("tls.selected_version=TLS1.3", completed.stdout)
        self.assertIn("sql.visibility=opaque", completed.stdout)

    def test_generated_fragmented_capture_is_reassembled_by_real_tshark(self) -> None:
        # Given: a synthetic capture with a split header and coalesced frames.
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = Path(temporary_directory) / "fragmented.pcap"
            write_fragmented_capture(capture)

            # When: the public CLI delegates reassembly to real TShark.
            completed = self._run_general(str(capture))

        # Then: both application messages survive reassembly and analysis.
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("frames=1,2,3 seq_ack=valid", completed.stdout)
        self.assertIn("mode=DEMO status=NORMAL osi_layers=7", completed.stdout)

    def test_general_cli_fails_cleanly_for_missing_inputs(self) -> None:
        # Given: either a missing capture or a missing TShark executable.
        cases = (
            ("missing-capture.pcapng", TSHARK, "capture file not found"),
            ("demo_tcp_9090_20261005.pcapng", "missing-tshark-for-test", "unable to start"),
        )

        for capture, tshark, expected in cases:
            with self.subTest(expected=expected):
                # When: the general CLI is invoked with the missing input.
                completed = self._run_general(capture, tshark)

                # Then: it exits nonzero with an actionable bounded error.
                self.assertEqual(completed.returncode, 1)
                self.assertIn(expected, completed.stderr)
                self.assertNotIn("Traceback", completed.stderr)


class PacketParsingTests(unittest.TestCase):
    def test_raw_follow_reassembles_segmented_and_coalesced_frames(self) -> None:
        # Given: direction-tagged follow output with a split header and two frames.
        request = encode_frame({"type": "hello"}) + encode_frame({"type": "diagnose"})
        response = encode_frame({"type": "hello_ok"})
        follow_text = "\n".join(
            (
                "=" * 67, "Follow: tcp,raw",
                "Filter: tcp.stream eq 7",
                "Node 0: 127.0.0.1:50000",
                "Node 1: 127.0.0.1:9090",
                request[:2].hex(),
                request[2:].hex(),
                "\t" + response.hex(),
                "=" * 67,
            )
        )

        # When: raw follow output and both directions are decoded.
        followed = parse_follow_raw(follow_text, 7)
        requests = decode_framed_messages(followed.node0_bytes)
        responses = decode_framed_messages(followed.node1_bytes)

        # Then: segmentation and coalescing do not change message boundaries.
        self.assertEqual([message["type"] for message in requests], ["hello", "diagnose"])
        self.assertEqual([message["type"] for message in responses], ["hello_ok"])

    def test_frame_decoder_rejects_truncated_streams(self) -> None:
        # Given: streams truncated inside either the header or the body.
        cases = (b"\x00\x00", b"\x00\x00\x00\x05{}")

        for data in cases:
            with self.subTest(data=data):
                # When/Then: frame decoding rejects the incomplete stream.
                with self.assertRaises(PacketDataError):
                    decode_framed_messages(data)

    def test_frame_decoder_rejects_invalid_json_values(self) -> None:
        # Given: malformed values plus bounded integer and nesting attacks.
        large_integer = b'{"value":' + (b"1" * 4_301) + b'}'
        integer_frame = len(large_integer).to_bytes(4, "big") + large_integer
        deep_depth = sys.getrecursionlimit() + 100
        deeper_object = (b'{"value":' * deep_depth) + b"0" + (b"}" * deep_depth)
        payloads = (
            b'{"value":NaN}', b'{"x":1,"x":2}',
            b"[]",
            b"\xff",
            deeper_object,
        )
        try:
            parsed_integer = json.loads(large_integer)
        except ValueError:
            payloads += (large_integer,)
        else:
            self.assertEqual(decode_framed_messages(integer_frame)[0], parsed_integer)

        for payload in payloads:
            with self.subTest(payload=payload):
                framed = len(payload).to_bytes(4, "big") + payload

                # When/Then: the untrusted payload cannot become a protocol message.
                with self.assertRaises(PacketDataError):
                    decode_framed_messages(framed)

        # When/Then: a runtime JSON ValueError is normalized at the boundary.
        with patch.object(json, "loads", side_effect=ValueError("digit limit")):
            self.assertRaises(PacketDataError, decode_framed_messages, b"\0\0\0\2{}")

        # When/Then: conversion-only recursion is normalized independently of depth.
        with patch("scripts.packet_parsing._json_value", side_effect=RecursionError):
            self.assertRaises(PacketDataError, decode_framed_messages, b"\0\0\0\2{}")

    def test_capture_report_type_hints_resolve(self) -> None:
        # Given: a runtime consumer resolving postponed CaptureReport annotations.
        hints = get_type_hints(CaptureReport)

        # When/Then: the capture annotation resolves to the concrete Path type.
        self.assertEqual(hints["capture"], Path)

    def test_follow_and_field_parsers_reject_malformed_output(self) -> None:
        # Given: invalid hex follow data and a field row with missing columns.
        follow_text = "\n".join(
            (
                "Follow: tcp,raw",
                "Filter: tcp.stream eq 1",
                "Node 0: 127.0.0.1:50000",
                "Node 1: 127.0.0.1:9090",
                "not-hex",
            )
        )
        field_text = "\t".join(TSHARK_FIELDS) + "\n1\t0"

        # When/Then: both malformed TShark surfaces fail closed.
        with self.assertRaises(PacketDataError):
            parse_follow_raw(follow_text, 1)
        with self.assertRaises(PacketDataError):
            parse_tshark_fields(field_text)

    def test_follow_rejects_decoded_stream_overflow(self) -> None:
        # Given: valid raw follow output whose decoded bytes exceed the cap.
        follow_text = "\n".join(
            (
                "Follow: tcp,raw",
                "Filter: tcp.stream eq 1",
                "Node 0: 127.0.0.1:50000",
                "Node 1: 127.0.0.1:9090",
                "00112233",
            )
        )

        # When: decoding is limited to three bytes.
        with self.assertRaises(PacketDataError) as raised:
            parse_follow_raw(follow_text, 1, decoded_limit=3)

        # Then: overflow is rejected before protocol parsing.
        self.assertEqual(raised.exception.code, "decoded_limit")


class BoundedProcessTests(unittest.TestCase):
    def test_runner_kills_timed_out_process(self) -> None:
        # Given: a child that exceeds an injected short deadline.
        limits = CommandLimits(0.1, 1_024, 1_024)
        started = time.monotonic()

        # When: the bounded runner waits for the child.
        with self.assertRaises(BoundedCommandError) as raised:
            run_bounded([sys.executable, "-c", "import time; time.sleep(30)"], limits)

        # Then: timeout is reported without waiting for the child duration.
        self.assertEqual(raised.exception.kind, "timeout")
        self.assertLess(time.monotonic() - started, 3)

    def test_runner_kills_stdout_and_stderr_overflow(self) -> None:
        # Given: children that exceed each independent output cap.
        cases = (
            ("import sys; sys.stdout.buffer.write(b'x'*4096); sys.stdout.flush()", "stdout_limit"),
            ("import sys; sys.stderr.buffer.write(b'x'*4096); sys.stderr.flush()", "stderr_limit"),
        )

        for program, expected_kind in cases:
            with self.subTest(expected_kind=expected_kind):
                # When: the bounded runner reads the oversized output.
                with self.assertRaises(BoundedCommandError) as raised:
                    run_bounded(
                        [sys.executable, "-c", program],
                        CommandLimits(5, 512, 512),
                    )

                # Then: the responsible stream cap is named.
                self.assertEqual(raised.exception.kind, expected_kind)

    def test_runner_reports_nonzero_exit_and_safe_stderr(self) -> None:
        # Given: a child with a diagnostic and a nonzero exit status.
        program = "import sys; sys.stderr.write('safe diagnostic'); sys.exit(7)"

        # When: the bounded runner observes command failure.
        with self.assertRaises(BoundedCommandError) as raised:
            run_bounded([sys.executable, "-c", program])

        # Then: exit status and bounded stderr are actionable.
        self.assertEqual(raised.exception.kind, "command_failed")
        self.assertIn("exited 7: safe diagnostic", raised.exception.detail)


class _PacketRunner:
    def __init__(self, packets: tuple[TcpPacket, ...]) -> None:
        self._packets = packets

    def packets(self, request: CaptureRequest) -> tuple[TcpPacket, ...]:
        return self._packets

    def follow(self, query: FollowQuery) -> NoReturn:
        raise AssertionError(f"unexpected follow call for stream {query.stream}")


class StreamLimitTests(unittest.TestCase):
    def test_analysis_rejects_more_than_128_streams_before_following(self) -> None:
        # Given: field evidence naming 129 distinct matching streams.
        packets = tuple(
            TcpPacket(
                index,
                index,
                Endpoint("127.0.0.1", 40_000 + index),
                Endpoint("127.0.0.1", 9_090),
                True,
                False,
                False,
                1,
                0,
                0,
                TransportFlags(False, False, False, False),
                False,
                "",
                False,
                "",
                "",
            )
            for index in range(129)
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = Path(temporary_directory) / "bounded.pcap"
            capture.write_bytes(b"fixture")

            # When: capture analysis applies its stream bound.
            with self.assertRaises(PacketDataError) as raised:
                analyze_capture(CaptureRequest(capture, 9_090, 3_307), _PacketRunner(packets))

        # Then: it rejects before launching any follow subprocess.
        self.assertEqual(raised.exception.code, "stream_limit")

    def test_analysis_rejects_conflicting_overlap_data(self) -> None:
        # Given: a valid handshake whose SYN carries conflicting overlap evidence.
        client = Endpoint("127.0.0.1", 50_000)
        server = Endpoint("127.0.0.1", 9_090)
        clean = TransportFlags(False, False, False, False)
        packets = (
            TcpPacket(
                1, 0, client, server, True, False, False, 1_000, 0, 0,
                clean, True, "", False, "", "",
            ),
            TcpPacket(
                2, 0, server, client, True, True, False, 2_000, 1_001, 0,
                clean, False, "", False, "", "",
            ),
            TcpPacket(
                3, 0, client, server, False, True, False, 1_001, 2_001, 0,
                clean, False, "", False, "", "",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = Path(temporary_directory) / "conflict.pcap"
            capture.write_bytes(b"fixture")

            # When: analysis reaches transport integrity checks.
            with self.assertRaises(PacketDataError) as raised:
                analyze_capture(CaptureRequest(capture, 9_090, 3_307), _PacketRunner(packets))

        # Then: conflicting bytes prevent a successful analysis claim.
        self.assertEqual(raised.exception.code, "overlap_conflict")


if __name__ == "__main__":
    unittest.main()
