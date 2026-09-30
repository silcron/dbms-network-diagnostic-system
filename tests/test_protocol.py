from __future__ import annotations

import socket
import threading
import unittest
from unittest.mock import MagicMock, patch

from app.client_core import DiagnosticClient
from app.protocol import PROTOCOL_VERSION, decode_payload, encode_message, recv_message, send_message
from app.server import DiagnosticHandler, DiagnosticServer


class ProtocolTests(unittest.TestCase):
    def test_connect_failure_closes_socket(self) -> None:
        fake_socket = MagicMock()
        client = DiagnosticClient()
        with patch("app.client_core.socket.create_connection", return_value=fake_socket):
            with patch("app.client_core.recv_message", side_effect=ConnectionError("closed")):
                with self.assertRaises(ConnectionError):
                    client.connect()
        fake_socket.close.assert_called_once()
        self.assertIsNone(client.sock)

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
