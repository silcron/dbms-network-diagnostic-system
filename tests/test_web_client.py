"""End-to-end checks for browser HTTP -> TCP server -> diagnostic response."""

from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from app.server import DiagnosticHandler, DiagnosticServer
from app.web_client import WebClientHandler


class WebClientIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tcp_server = DiagnosticServer(("127.0.0.1", 0), DiagnosticHandler)
        self.web_server = ThreadingHTTPServer(("127.0.0.1", 0), WebClientHandler)
        self.threads = [
            threading.Thread(target=server.serve_forever, daemon=True)
            for server in (self.tcp_server, self.web_server)
        ]
        for thread in self.threads:
            thread.start()

    def tearDown(self) -> None:
        for server in (self.web_server, self.tcp_server):
            server.shutdown()
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=2)

    def post_payload(self, body: object) -> tuple[int, dict]:
        payload = json.dumps(body).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.web_server.server_address[1]}/api/diagnose",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def post(self, mode: str, scenario: str = "normal") -> tuple[int, dict]:
        return self.post_payload({
            "host": "127.0.0.1",
            "port": self.tcp_server.server_address[1],
            "mode": mode,
            "demo_scenario": scenario,
        })

    def test_demo_statuses_are_labelled_and_include_seven_layers(self) -> None:
        for scenario, expected in (("normal", "NORMAL"), ("warning", "WARNING"), ("down", "DOWN")):
            with self.subTest(scenario=scenario):
                status_code, data = self.post("demo", scenario)
                self.assertEqual(status_code, 200)
                self.assertEqual(data["result"]["mode"], "DEMO")
                self.assertEqual(data["result"]["status"], expected)
                self.assertIn("실제 DB 연결은 확인하지 않았습니다", data["result"]["detail"])
                self.assertEqual(len(data["osi_trace"]), 7)

    def test_live_without_configuration_is_not_a_fake_success(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            status_code, data = self.post("live")
        self.assertEqual(status_code, 200)
        self.assertEqual(data["result"]["mode"], "LIVE")
        self.assertEqual(data["result"]["status"], "WARNING")
        self.assertIn("DB_HOST", data["result"]["detail"])

    def test_invalid_mode_is_rejected(self) -> None:
        status_code, data = self.post("invalid")
        self.assertEqual(status_code, 400)
        self.assertIn("mode must be live or demo", data["error"])

    def test_invalid_port_and_non_object_json_are_rejected(self) -> None:
        for body in ({"port": 0}, []):
            with self.subTest(body=body):
                status_code, _ = self.post_payload(body)
                self.assertEqual(status_code, 400)

    def test_oversized_request_body_is_rejected(self) -> None:
        status_code, data = self.post_payload({"padding": "x" * 5000})
        self.assertEqual(status_code, 400)
        self.assertIn("4096", data["error"])

    def test_page_shows_the_tcp_client_server_path(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.web_server.server_address[1]}/", timeout=3) as response:
            page = response.read().decode("utf-8")
        self.assertIn("웹 클라이언트", page)
        self.assertIn("진단 서버", page)
        self.assertIn("TCP 연결", page)


if __name__ == "__main__":
    unittest.main()
