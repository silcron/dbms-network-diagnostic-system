from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch

from app.web_client import DiagnosticWebServer, MAX_HTTP_BODY_BYTES, WebClientHandler
from app.web_inputs import HttpInputError


class WebTransferEncodingRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.headers_read = threading.Event()
        self.body_sent = threading.Event()
        headers_read = self.headers_read
        body_sent = self.body_sent

        class StagedUploadHandler(WebClientHandler):
            def _read_request_body(self) -> bytes:
                headers_read.set()
                if not body_sent.wait(timeout=3):
                    raise TimeoutError("test upload did not reach the rejection boundary")
                return super()._read_request_body()

        self.server = DiagnosticWebServer(("127.0.0.1", 0), StagedUploadHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.body_sent.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())

    def test_late_unsupported_upload_returns_complete_bad_request(self) -> None:
        # Given: headers arrive separately from an unsupported upload body.
        with patch.object(WebClientHandler, "_diagnose") as diagnose:
            with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=4) as client:
                client.sendall(
                    b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                )
                self.assertTrue(self.headers_read.wait(timeout=3))
                # When: submitted bytes remain unread at the rejection boundary.
                client.sendall(b"4\r\n{}\r\n0\r\n\r\n")
                self.body_sent.set()
                with client.makefile("rb") as response_file:
                    response = response_file.read()
            # Then: the client receives the full safe error without invoking upstream.
            self.assertIn(b" 400 ", response.partition(b"\r\n")[0])
            payload = json.loads(response.partition(b"\r\n\r\n")[2])
            self.assertEqual(payload["code"], "invalid_request")
            diagnose.assert_not_called()

    def test_unsupported_upload_without_body_returns_before_client_timeout(self) -> None:
        # Given: a client declares unsupported transfer encoding but sends no body.
        with patch.object(WebClientHandler, "_diagnose") as diagnose:
            with socket.create_connection(("127.0.0.1", self.server.server_port), timeout=4) as client:
                client.sendall(
                    b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                    b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                )
                self.assertTrue(self.headers_read.wait(timeout=3))
                # When: rejection runs while the upload connection stays open.
                started = time.monotonic()
                self.body_sent.set()
                with client.makefile("rb") as response_file:
                    response = response_file.read()
                elapsed = time.monotonic() - started
            # Then: the whole-body deadline yields a safe response without hanging.
            self.assertIn(b" 400 ", response.partition(b"\r\n")[0])
            self.assertEqual(json.loads(response.partition(b"\r\n\r\n")[2])["code"], "invalid_request")
            self.assertLess(elapsed, 2.8)
            diagnose.assert_not_called()


class WebTransferEncodingDiscardLimitTests(unittest.TestCase):
    def test_unsupported_upload_discards_at_most_body_byte_limit(self) -> None:
        # Given: more opaque upload bytes than the allowed rejection drain budget.
        body = BytesIO(b"x" * (MAX_HTTP_BODY_BYTES + 1))
        handler = WebClientHandler.__new__(WebClientHandler)
        handler.headers = Message()
        handler.headers["Transfer-Encoding"] = "chunked"
        handler.rfile = body
        with socket.socket() as connection:
            handler.connection = connection
            # When: unsupported encoding is rejected at the request boundary.
            with self.assertRaises(HttpInputError):
                handler._read_request_body()
        # Then: only the bounded opaque prefix was consumed; encoding stays unsupported.
        self.assertEqual(body.tell(), MAX_HTTP_BODY_BYTES)


if __name__ == "__main__":
    unittest.main()
