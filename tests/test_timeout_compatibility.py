from __future__ import annotations

import json
import socket
import threading
import unittest
from contextlib import closing
from email.message import Message
from http.client import HTTPConnection
from io import BytesIO
from unittest.mock import patch

from app.protocol import recv_message, send_message
from app.web_client import DiagnosticWebServer, WebClientHandler
from app.web_inputs import HttpInputError


class LegacySocketTimeout(OSError):
    pass


class TimeoutBody(BytesIO):
    def read1(self, size: int = -1) -> bytes:
        raise LegacySocketTimeout("timed out")


class TimeoutCompatibilityTests(unittest.TestCase):
    def test_receive_normalizes_legacy_timeout_and_restores_socket(self) -> None:
        sender, receiver = socket.socketpair()
        with sender, receiver:
            receiver.settimeout(0.7)
            with patch("app.protocol.socket.timeout", LegacySocketTimeout):
                with patch.object(socket.socket, "recv", side_effect=LegacySocketTimeout("timed out")):
                    with self.assertRaises(TimeoutError) as caught:
                        recv_message(receiver, idle_timeout=0.05)
            self.assertIsInstance(caught.exception.__cause__, LegacySocketTimeout)
            self.assertEqual(receiver.gettimeout(), 0.7)

    def test_send_normalizes_legacy_timeout_and_restores_socket(self) -> None:
        sender, receiver = socket.socketpair()
        with sender, receiver:
            sender.settimeout(0.7)
            with patch("app.protocol.socket.timeout", LegacySocketTimeout):
                with patch.object(socket.socket, "send", side_effect=LegacySocketTimeout("timed out")):
                    with self.assertRaises(TimeoutError) as caught:
                        send_message(sender, {"type": "hello"}, timeout=0.05)
            self.assertIsInstance(caught.exception.__cause__, LegacySocketTimeout)
            self.assertEqual(sender.gettimeout(), 0.7)

    def test_request_body_legacy_timeout_is_safe_input_failure(self) -> None:
        sender, receiver = socket.socketpair()
        with sender, receiver, TimeoutBody() as body:
            handler = WebClientHandler.__new__(WebClientHandler)
            handler.connection = receiver
            handler.rfile = body
            handler.headers = Message()
            handler.headers["Content-Length"] = "10"
            receiver.settimeout(0.7)
            with patch("socket.timeout", LegacySocketTimeout):
                with self.assertRaises(HttpInputError) as caught:
                    handler._read_request_body()
            self.assertEqual(caught.exception.code, "invalid_request")
            self.assertIsInstance(caught.exception.__cause__, LegacySocketTimeout)
            self.assertEqual(receiver.gettimeout(), 0.7)

    def test_legacy_connect_timeout_is_http_504_with_timeout_metric(self) -> None:
        with DiagnosticWebServer(("127.0.0.1", 0), WebClientHandler) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with patch("socket.timeout", LegacySocketTimeout):
                    with patch("app.web_client.DiagnosticClient.connect", side_effect=LegacySocketTimeout("timed out")):
                        with closing(HTTPConnection("127.0.0.1", server.server_port, timeout=3)) as client:
                            client.request("POST", "/api/diagnose", json.dumps({"mode": "demo"}))
                            response = client.getresponse()
                            self.assertEqual(response.status, 504)
                            self.assertEqual(json.loads(response.read())["code"], "upstream_timeout")
                self.assertIn('outcome="upstream_timeout"} 1', server.metrics.render())
            finally:
                server.shutdown()
                worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
