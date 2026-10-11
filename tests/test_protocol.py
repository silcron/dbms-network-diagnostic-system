from __future__ import annotations

import socket
import struct
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import MagicMock, patch

from app.client_core import DiagnosticClient
from app.protocol import (
    PROTOCOL_VERSION,
    JsonObject,
    JsonValue,
    ProtocolError,
    decode_payload,
    encode_message,
    recv_message,
    send_message,
)
from app.server import DiagnosticHandler, DiagnosticServer


class ProtocolTests(unittest.TestCase):
    def test_connect_failure_closes_socket(self) -> None:
        fake_socket = MagicMock()
        client = DiagnosticClient()
        with patch("app.client_core.socket.create_connection", return_value=fake_socket), patch(
            "app.client_core.send_message"
        ), patch("app.client_core.recv_message", side_effect=ConnectionError("closed")):
                with self.assertRaises(ConnectionError):
                    client.connect()
        fake_socket.close.assert_called_once()
        self.assertIsNone(client.sock)

    def test_connect_rejects_invalid_hello_fields_and_closes_socket(self) -> None:
        invalid_responses = (
            {"type": "hello_ok", "protocol_version": PROTOCOL_VERSION + 1, "session_id": "token"},
            {"type": "hello_ok", "protocol_version": PROTOCOL_VERSION, "session_id": ""},
            {"type": "hello_ok", "protocol_version": PROTOCOL_VERSION, "session_id": 7},
        )
        for response in invalid_responses:
            with self.subTest(response=response):
                # Given: a server hello with an invalid version or session token.
                fake_socket = MagicMock()
                client = DiagnosticClient()
                with patch("app.client_core.socket.create_connection", return_value=fake_socket), patch(
                    "app.client_core.send_message"
                ), patch("app.client_core.recv_message", return_value=response):
                    # When/Then: the protocol violation closes the new connection.
                    with self.assertRaises(ProtocolError):
                        client.connect()
                fake_socket.close.assert_called_once()
                self.assertIsNone(client.sock)
                self.assertIsNone(client.session_id)

    def test_logout_failure_closes_socket(self) -> None:
        fake_socket = MagicMock()
        client = DiagnosticClient()
        client.sock = fake_socket
        client.session_id = "temporary-session"
        with patch("app.client_core.send_message", side_effect=OSError("closed")):
            with self.assertRaises(OSError):
                client.logout()
        fake_socket.close.assert_called_once()
        self.assertIsNone(client.sock)
        self.assertIsNone(client.session_id)

    def test_diagnose_send_failure_preserves_exception_and_clears_state(self) -> None:
        # Given: an established client whose next send fails.
        failure = OSError("send failed")
        fake_socket = MagicMock()
        client = DiagnosticClient()
        client.sock = fake_socket
        client.session_id = "temporary-session"

        # When: diagnose reports the transport failure.
        with patch("app.client_core.send_message", side_effect=failure):
            with self.assertRaises(OSError) as caught:
                client.diagnose()

        # Then: the exact exception survives and the unusable connection is cleared.
        self.assertIs(caught.exception, failure)
        fake_socket.close.assert_called_once()
        self.assertIsNone(client.sock)
        self.assertIsNone(client.session_id)

    def test_diagnose_receive_protocol_failure_preserves_exception_and_clears_state(self) -> None:
        # Given: an established client whose response is invalid protocol data.
        failure = ProtocolError("invalid response")
        fake_socket = MagicMock()
        client = DiagnosticClient()
        client.sock = fake_socket
        client.session_id = "temporary-session"

        # When: diagnose receives the protocol failure.
        with patch("app.client_core.send_message"), patch("app.client_core.recv_message", side_effect=failure):
            with self.assertRaises(ProtocolError) as caught:
                client.diagnose()

        # Then: the exact exception survives and the unusable connection is cleared.
        self.assertIs(caught.exception, failure)
        fake_socket.close.assert_called_once()
        self.assertIsNone(client.sock)
        self.assertIsNone(client.session_id)

    def test_encode_decode_korean_json(self) -> None:
        original = {"type": "hello", "message": "안녕하세요"}
        encoded = encode_message(original)
        self.assertEqual(decode_payload(encoded[4:]), original)

    def test_socket_round_trip(self) -> None:
        left, right = socket.socketpair()
        try:
            send_message(left, {"type": "ping", "value": 42})
            self.assertEqual(recv_message(right), {"type": "ping", "value": 42})
        finally:
            left.close()
            right.close()


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = DiagnosticServer(("127.0.0.1", 0), DiagnosticHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())

    def raw_connection(self) -> socket.socket:
        host, port = self.server.server_address
        return socket.create_connection((host, port), timeout=1)

    def hello(self, connection: socket.socket) -> JsonObject:
        send_message(connection, {"type": "hello"})
        return recv_message(connection)

    def logout(self, connection: socket.socket, session_id: JsonValue) -> JsonObject:
        send_message(connection, {"type": "logout", "session_id": session_id})
        return recv_message(connection)

    def session_count(self) -> int:
        with self.server.sessions.lock:
            return len(self.server.sessions.values)

    def test_repeated_hello_reuses_one_token_and_logout_acknowledges_then_closes(self) -> None:
        # Given: one TCP connection that sends hello twice.
        with closing(self.raw_connection()) as connection:
            first = self.hello(connection)

            # When: the same connection repeats hello and then logs out.
            second = self.hello(connection)
            acknowledgement = self.logout(connection, second["session_id"])
            end_of_stream = connection.recv(1)

            # Then: only one token exists, logout is acknowledged, and the server closes.
            self.assertEqual(second["session_id"], first["session_id"])
            self.assertEqual(acknowledgement["type"], "logout_ok")
            self.assertEqual(end_of_stream, b"")
        self.assertEqual(self.session_count(), 0)

    def test_foreign_connection_token_is_rejected(self) -> None:
        # Given: two live TCP connections with distinct sessions.
        with closing(self.raw_connection()) as first_connection, closing(self.raw_connection()) as second_connection:
            first = self.hello(first_connection)
            second = self.hello(second_connection)

            # When: the second connection presents the first connection's token.
            send_message(
                second_connection,
                {"type": "diagnose", "session_id": first["session_id"], "mode": "demo"},
            )
            response = recv_message(second_connection)

            # Then: the request has a stable invalid-session error and both sessions remain usable.
            self.assertEqual(response["type"], "error")
            self.assertEqual(response["code"], "invalid_session")
            self.assertEqual(self.logout(first_connection, first["session_id"])["type"], "logout_ok")
            self.assertEqual(self.logout(second_connection, second["session_id"])["type"], "logout_ok")
            self.assertEqual(first_connection.recv(1), b"")
            self.assertEqual(second_connection.recv(1), b"")
        self.assertEqual(self.session_count(), 0)

    def test_invalid_request_fields_are_not_coerced(self) -> None:
        # Given: an authenticated TCP connection.
        with closing(self.raw_connection()) as connection:
            hello = self.hello(connection)
            invalid_requests = (
                ({"type": "diagnose", "session_id": hello["session_id"], "mode": 1}, "invalid_mode"),
                (
                    {
                        "type": "diagnose",
                        "session_id": hello["session_id"],
                        "mode": "demo",
                        "demo_scenario": 1,
                    },
                    "invalid_scenario",
                ),
                ({"session_id": hello["session_id"]}, "invalid_request"),
                ({"type": "unknown", "session_id": hello["session_id"]}, "unsupported_type"),
            )

            for request, expected_code in invalid_requests:
                with self.subTest(expected_code=expected_code):
                    # When: a malformed request crosses the protocol boundary.
                    send_message(connection, request)
                    response = recv_message(connection)

                    # Then: the response exposes the frozen machine code.
                    self.assertEqual(response["type"], "error")
                    self.assertEqual(response["code"], expected_code)

            self.logout(connection, hello["session_id"])
            self.assertEqual(connection.recv(1), b"")
        self.assertEqual(self.session_count(), 0)

    def test_idle_expiry_removes_connection_session(self) -> None:
        # Given: a server with a short injected idle timeout and one authenticated peer.
        self.server.idle_timeout_seconds = 0.05
        with closing(self.raw_connection()) as connection:
            self.hello(connection)

            # When: the peer sends no next frame until the server's idle budget expires.
            end_of_stream = connection.recv(1)

            # Then: the handler closes and removes its connection-owned session.
            self.assertEqual(end_of_stream, b"")
        self.assertEqual(self.session_count(), 0)

    def test_reset_peer_removes_connection_session(self) -> None:
        # Given: an authenticated peer configured to reset on close.
        connection = self.raw_connection()
        self.hello(connection)
        connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))

        # When: the peer resets the TCP connection without logout.
        connection.close()

        # Then: the handler removes the session within a bounded observation window.
        deadline = time.monotonic() + 1.0
        while self.session_count() and time.monotonic() < deadline:
            threading.Event().wait(0.01)
        self.assertEqual(self.session_count(), 0)

    def test_partial_header_and_body_deadlines_remove_session(self) -> None:
        # Given: authenticated peers and a short whole-frame timeout.
        self.server.frame_timeout_seconds = 0.05
        partial_frames = (b"\x00", struct.pack("!I", 2) + b"{")
        for partial_frame in partial_frames:
            with self.subTest(partial_frame=partial_frame), closing(self.raw_connection()) as connection:
                self.hello(connection)

                # When: a header or body stops before its declared frame is complete.
                connection.sendall(partial_frame)
                end_of_stream = connection.recv(1)

                # Then: the deadline closes the connection and removes its token.
                self.assertEqual(end_of_stream, b"")
            self.assertEqual(self.session_count(), 0)

    def test_client_connects_and_receives_diagnostic(self) -> None:
        host, port = self.server.server_address
        client = DiagnosticClient(host, port)
        try:
            hello = client.connect()
            self.assertEqual(hello["type"], "hello_ok")
            self.assertEqual(hello["protocol_version"], PROTOCOL_VERSION)
            response = client.diagnose()
            self.assertEqual(response["type"], "diagnostic_result")
            self.assertEqual(response["result"]["mode"], "DEMO")
            self.assertEqual(len(response["osi_trace"]), 7)
            self.assertGreaterEqual(response["client_rtt_ms"], 0)
        finally:
            client.logout()

    def test_demo_connection_failure_can_be_presented(self) -> None:
        host, port = self.server.server_address
        client = DiagnosticClient(host, port)
        try:
            client.connect()
            response = client.diagnose("down", mode="demo")
            self.assertEqual(response["result"]["mode"], "DEMO")
            self.assertEqual(response["result"]["status"], "DOWN")
        finally:
            client.logout()

    def test_live_request_without_database_configuration_is_not_simulated(self) -> None:
        host, port = self.server.server_address
        client = DiagnosticClient(host, port)
        try:
            client.connect()
            response = client.diagnose(mode="live")
            self.assertEqual(response["result"]["mode"], "LIVE")
            self.assertEqual(response["result"]["status"], "WARNING")
        finally:
            client.logout()


if __name__ == "__main__":
    unittest.main()
