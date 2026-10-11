from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from app.client_core import DiagnosticClient
from app.protocol import (
    MAX_MESSAGE_BYTES,
    JsonValue,
    ProtocolError,
    decode_payload,
    encode_message,
    recv_message,
    send_message,
)


class TransportTests(unittest.TestCase):
    def test_protocol_imports_without_python39_typing_typealias(self) -> None:
        # Given: a subprocess that rejects imports of Python 3.10's TypeAlias.
        workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "import builtins, sys; real_import = builtins.__import__; "
                "builtins.__import__ = lambda name, globals=None, locals=None, fromlist=(), level=0: (_ for _ in ()).throw(ImportError('TypeAlias is unavailable')) if name == 'typing' and 'TypeAlias' in fromlist else real_import(name, globals, locals, fromlist, level); "
                "sys.modules.pop('app.protocol', None); import app.protocol; print('PY39_TYPING_SURFACE_IMPORT_OK')",
            ],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

        # Then: the protocol imports and emits only its exact success marker.
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertEqual(result.stdout, "PY39_TYPING_SURFACE_IMPORT_OK\n")

    def test_recv_reassembles_segmented_and_coalesced_frames(self) -> None:
        # Given: two frames whose writes split the first header and coalesce both payloads.
        sender, receiver = socket.socketpair()
        first = encode_message({"type": "first", "value": 1})
        second = encode_message({"type": "second", "value": 2})
        try:
            sender.sendall(first[:1])
            sender.sendall(first[1:7])
            sender.sendall(first[7:] + second)

            # When: the receiver reads two protocol messages.
            first_message = recv_message(receiver)
            second_message = recv_message(receiver)

            # Then: TCP write boundaries do not change frame boundaries.
            self.assertEqual(first_message, {"type": "first", "value": 1})
            self.assertEqual(second_message, {"type": "second", "value": 2})
        finally:
            sender.close()
            receiver.close()

    def test_recv_rejects_truncated_frame(self) -> None:
        # Given: a peer advertises five payload bytes but closes after two.
        sender, receiver = socket.socketpair()
        try:
            sender.sendall(struct.pack("!I", 5) + b"{}")
            sender.close()

            # When/Then: the incomplete frame is reported as a closed connection.
            with self.assertRaises(ConnectionError):
                recv_message(receiver)
        finally:
            sender.close()
            receiver.close()

    def test_recv_rejects_zero_and_oversized_lengths(self) -> None:
        for length in (0, MAX_MESSAGE_BYTES + 1):
            with self.subTest(length=length):
                # Given: an invalid four-byte frame length.
                sender, receiver = socket.socketpair()
                try:
                    sender.sendall(struct.pack("!I", length))

                    # When/Then: the frame is rejected before a body read.
                    with self.assertRaises(ProtocolError):
                        recv_message(receiver)
                finally:
                    sender.close()
                    receiver.close()

    def test_json_rejects_nonfinite_values_in_both_directions(self) -> None:
        # Given/When/Then: outbound and inbound non-finite numbers are invalid JSON.
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(direction="encode", value=value):
                with self.assertRaises(ProtocolError):
                    encode_message({"value": value})
        for token in (b"NaN", b"Infinity", b"-Infinity", b"1e309"):
            with self.subTest(direction="decode", token=token):
                with self.assertRaises(ProtocolError):
                    decode_payload(b'{"value":' + token + b"}")

    def test_json_rejects_duplicate_object_keys(self) -> None:
        # Given: a nested JSON object repeats a key.
        payload = b'{"outer":{"value":1,"value":2}}'

        # When/Then: parsing rejects the ambiguous object.
        with self.assertRaises(ProtocolError):
            decode_payload(payload)

    def test_json_rejects_invalid_utf8_and_non_object(self) -> None:
        for payload in (b"\xff", b"[]", b'"text"'):
            with self.subTest(payload=payload):
                # Given/When/Then: malformed UTF-8 or a non-object JSON value is rejected.
                with self.assertRaises(ProtocolError):
                    decode_payload(payload)

    def test_json_decode_normalizes_integer_and_recursion_limits(self) -> None:
        # Given: valid JSON inputs that exceed separate CPython codec limits.
        large_integer_payload = b'{"value":' + (b"9" * 4301) + b'}'
        json.loads(large_integer_payload, parse_int=str)
        payloads = {
            "decoder-recursion": b'{"value":' + (b"[" * 20_000) + b"0" + (b"]" * 20_000) + b"}",
        }
        if sys.version_info >= (3, 11) and sys.get_int_max_str_digits() > 0:
            payloads["integer-conversion"] = large_integer_payload
        for boundary, payload in payloads.items():
            with self.subTest(boundary=boundary):
                # When/Then: codec implementation failures stay protocol errors.
                with self.assertRaises((ValueError, RecursionError)) as caught:
                    decode_payload(payload)
                self.assertIs(type(caught.exception), ProtocolError)

    def test_json_encode_normalizes_recursion_limit(self) -> None:
        # Given: a JSON-compatible value beyond the encoder's recursion limit.
        nested_value: JsonValue = 0
        for _ in range(20_000):
            nested_value = [nested_value]

        # When/Then: the codec implementation failure stays a protocol error.
        with self.assertRaises((ValueError, RecursionError)) as caught:
            encode_message({"value": nested_value})
        self.assertIs(type(caught.exception), ProtocolError)

    def test_diagnose_raw_codec_failure_is_protocol_error_and_clears_state(self) -> None:
        # Given: a connected client and a raw peer response that exceeds a codec limit.
        client_socket, peer_socket = socket.socketpair()
        peer_socket.settimeout(1.0)
        client = DiagnosticClient(timeout=1.0)
        client.sock = client_socket
        client.session_id = "raw-peer-session"
        response_value = b"9" * 4301 if sys.version_info >= (3, 11) and sys.get_int_max_str_digits() > 0 else (b"[" * 20_000) + b"0" + (b"]" * 20_000)
        response_payload = b'{"type":"diagnostic_result","result":{"value":' + response_value + b'}}'
        self.assertLess(len(response_payload), MAX_MESSAGE_BYTES)
        writer = threading.Thread(target=peer_socket.sendall, args=(struct.pack("!I", len(response_payload)) + response_payload,))
        writer.start()
        try:
            # When: diagnose receives the raw codec-limit payload over a real socket.
            with self.assertRaises(ValueError) as caught:
                client.diagnose()

            # Then: the exact normalized error escapes after client state is cleared.
            self.assertEqual(
                (type(caught.exception), client.sock is None, client.session_id),
                (ProtocolError, True, None),
            )
            self.assertEqual(recv_message(peer_socket)["type"], "diagnose")
            self.assertEqual(peer_socket.recv(1), b"")
        finally:
            client.close()
            peer_socket.close()
            writer.join(timeout=1)
            self.assertFalse(writer.is_alive())

    def test_recv_deadline_expires_during_slow_header_and_restores_timeout(self) -> None:
        # Given: a peer trickles a valid frame more slowly than the whole-frame budget.
        sender, receiver = socket.socketpair()
        receiver.settimeout(1.75)
        stop = threading.Event()

        def trickle() -> None:
            for byte in encode_message({"type": "slow"}):
                if stop.is_set():
                    return
                try:
                    sender.sendall(bytes((byte,)))
                except OSError:
                    return
                stop.wait(0.06)

        writer = threading.Thread(target=trickle)
        writer.start()
        try:
            # When: the receiver applies a 100 ms budget after the first byte.
            with self.assertRaises(TimeoutError):
                recv_message(receiver, idle_timeout=0.5, frame_timeout=0.1)

            # Then: slow progress cannot reset the budget and the old timeout is restored.
            self.assertEqual(receiver.gettimeout(), 1.75)
        finally:
            stop.set()
            sender.close()
            receiver.close()
            writer.join(timeout=1)
            self.assertFalse(writer.is_alive())

    def test_recv_idle_timeout_restores_timeout(self) -> None:
        # Given: a connected peer sends no first byte.
        sender, receiver = socket.socketpair()
        receiver.settimeout(1.25)
        try:
            # When: the idle budget expires.
            with self.assertRaises(TimeoutError):
                recv_message(receiver, idle_timeout=0.05, frame_timeout=0.5)

            # Then: the caller's prior timeout is restored.
            self.assertEqual(receiver.gettimeout(), 1.25)
        finally:
            sender.close()
            receiver.close()

    def test_recv_deadline_expires_during_slow_body_and_restores_timeout(self) -> None:
        # Given: a complete header followed by a body that trickles beyond the frame budget.
        sender, receiver = socket.socketpair()
        receiver.settimeout(1.125)
        frame = encode_message({"type": "slow-body"})
        stop = threading.Event()

        def trickle_body() -> None:
            try:
                sender.sendall(frame[:5])
                for byte in frame[5:]:
                    if stop.wait(0.06):
                        return
                    sender.sendall(bytes((byte,)))
            except OSError:
                return

        writer = threading.Thread(target=trickle_body)
        writer.start()
        try:
            # When: the body makes progress without completing inside 100 ms.
            with self.assertRaises(TimeoutError):
                recv_message(receiver, idle_timeout=0.5, frame_timeout=0.1)

            # Then: body progress cannot reset the budget and the timeout is restored.
            self.assertEqual(receiver.gettimeout(), 1.125)
        finally:
            stop.set()
            sender.close()
            receiver.close()
            writer.join(timeout=1)
            self.assertFalse(writer.is_alive())

    def test_send_restores_timeout_after_complete_frame(self) -> None:
        # Given: a socket with a caller-owned timeout.
        sender, receiver = socket.socketpair()
        sender.settimeout(1.5)
        try:
            # When: a bounded frame send succeeds.
            send_message(sender, {"type": "ping"}, timeout=0.2)

            # Then: the timeout and transmitted frame are both intact.
            self.assertEqual(sender.gettimeout(), 1.5)
            self.assertEqual(recv_message(receiver), {"type": "ping"})
        finally:
            sender.close()
            receiver.close()

    def test_send_deadline_expires_when_peer_does_not_read_and_restores_timeout(self) -> None:
        # Given: a real socket whose send buffer is already full because its peer does not read.
        sender, receiver = socket.socketpair()
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        sender.setblocking(False)
        while True:
            try:
                sender.send(b"x" * 65_536)
            except BlockingIOError:
                break
        sender.settimeout(1.875)
        try:
            # When: the bounded send fills the socket buffer.
            with self.assertRaises(TimeoutError):
                send_message(sender, {"type": "blocked"}, timeout=0.05)

            # Then: the whole-send deadline expires and restores the prior timeout.
            self.assertEqual(sender.gettimeout(), 1.875)
        finally:
            sender.close()
            receiver.close()


class ClientConfigurationTests(unittest.TestCase):
    def test_default_client_timeout_is_ten_seconds(self) -> None:
        # Given: no explicit timeout and no environment override.
        with patch.dict(os.environ, {}, clear=True):
            # When: a client is constructed.
            client = DiagnosticClient()

        # Then: the frozen client budget defaults to ten seconds.
        self.assertEqual(client.timeout, 10.0)

    def test_client_timeout_environment_and_explicit_precedence(self) -> None:
        # Given: a valid environment timeout.
        with patch.dict(os.environ, {"DIAGNOSTIC_TIMEOUT_SECONDS": "12.5"}):
            # When: clients omit or explicitly provide the timeout.
            configured = DiagnosticClient()
            explicit = DiagnosticClient(timeout=2.5)

        # Then: omission uses the environment and an explicit value wins.
        self.assertEqual(configured.timeout, 12.5)
        self.assertEqual(explicit.timeout, 2.5)

    def test_explicit_timeout_ignores_invalid_environment(self) -> None:
        # Given: invalid environment configuration and a valid explicit budget.
        with patch.dict(os.environ, {"DIAGNOSTIC_TIMEOUT_SECONDS": "NaN"}):
            # When: the client receives an explicit timeout.
            client = DiagnosticClient(timeout=3.0)

        # Then: the explicit value takes precedence without reading the environment.
        self.assertEqual(client.timeout, 3.0)

    def test_invalid_client_timeout_environment_is_rejected(self) -> None:
        for configured in ("NaN", "Infinity", "0", "-1", "120.1", "invalid"):
            with self.subTest(configured=configured):
                # Given: an invalid omitted-timeout configuration.
                with patch.dict(os.environ, {"DIAGNOSTIC_TIMEOUT_SECONDS": configured}):
                    # When/Then: construction fails clearly before opening a socket.
                    with self.assertRaises(ValueError):
                        DiagnosticClient()


if __name__ == "__main__":
    unittest.main()
