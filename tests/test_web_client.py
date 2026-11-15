"""End-to-end checks for browser HTTP -> TCP server -> diagnostic response."""

from __future__ import annotations

import json
import math
import os
import socket
import sys
import threading
import time
import unittest
from contextlib import closing
from email.message import Message
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.client_core import DiagnosticClient
from app.protocol import ProtocolError
from app.server import DiagnosticHandler, DiagnosticServer
from app.web_client import WebClientHandler
from app.web_inputs import JsonObject, JsonValue


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

    def post_payload(self, body: JsonValue) -> tuple[int, JsonObject]:
        payload = json.dumps(body, allow_nan=True).encode("utf-8")
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

    def post(self, mode: str, scenario: str = "normal") -> tuple[int, JsonObject]:
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

    def test_three_live_targets_overlap_and_return_independent_results(self) -> None:
        ready = threading.Barrier(3, timeout=1.5)
        settings = {"DB_WARNING_MS": "10000", "DB4_HOST": "unused"}
        for slot, prefix in enumerate(("DB_", "DB2_", "DB3_"), 1):
            settings.update({prefix + "HOST": "localhost", prefix + "PORT": str(3306 + slot),
                             prefix + "USER": f"user{slot}", prefix + "PASSWORD": f"secret{slot}"})
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (1,)

        def connect(*, port: int, user: str, password: str, **kwargs: object) -> MagicMock:
            ready.wait()
            slot = port - 3306
            self.assertEqual((user, password), (f"user{slot}", f"secret{slot}"))
            if slot == 2:
                raise RuntimeError(1045, "secret2")
            if slot == 3:
                raise ConnectionRefusedError("secret3")
            return connection

        with patch.dict(os.environ, settings, clear=True), patch.dict(sys.modules, {"pymysql": SimpleNamespace(connect=connect)}):
            status, data = self.post("live")

        self.assertEqual(status, 200)
        self.assertEqual([(db["target_id"], db["status"]) for db in data["results"]],
                         [(1, "NORMAL"), (2, "WARNING"), (3, "DOWN")])
        self.assertEqual(data["result"], data["results"][2])
        self.assertIn("우선 점검: DB 3", data["feedback_summary"])
        self.assertIn("DB2_USER", data["results"][1]["next_check"])
        self.assertIn("주소·포트", data["result"]["next_check"])
        self.assertNotIn("secret", json.dumps(data))
        cursor.execute.assert_called_once_with("SELECT 1")
        connection.close.assert_called_once()

    def test_live_preserves_sparse_slot_and_isolates_invalid_configuration(self) -> None:
        settings = {"DB2_HOST": "localhost", "DB2_PORT": "invalid", "DB3_HOST": "localhost"}
        with patch.dict(os.environ, settings, clear=True), patch.dict(sys.modules, {"pymysql": None}):
            status, data = self.post("live")
        self.assertEqual(status, 200)
        self.assertEqual([(db["target_id"], db["error_kind"]) for db in data["results"]],
                         [(2, "config"), (3, "dependency")])
        self.assertIn("DB2_PORT", data["results"][0]["detail"])

    def test_mixed_demo_returns_three_simulated_results(self) -> None:
        with patch.dict(os.environ, {"DB_HOST": "unreachable"}, clear=True):
            status, data = self.post("demo", "mixed")
        self.assertEqual(status, 200)
        self.assertEqual([(db["mode"], db["status"]) for db in data["results"]],
                         [("DEMO", "NORMAL"), ("DEMO", "WARNING"), ("DEMO", "DOWN")])

    def test_invalid_mode_is_rejected(self) -> None:
        status_code, data = self.post("invalid")
        self.assertEqual(status_code, 400)
        self.assertEqual(data["code"], "invalid_mode")
        self.assertEqual(data["message"], "mode must be live or demo")
        self.assertIn("mode must be live or demo", data["error"])

    def test_invalid_port_and_non_object_json_are_rejected(self) -> None:
        for body in ({"port": 0}, {"port": "9090"}, {"host": 127}, [], {"port": math.nan}):
            with self.subTest(body=body):
                status_code, data = self.post_payload(body)
                self.assertEqual(status_code, 400)
                self.assertIn(data["code"], {"invalid_request", "unsupported_type"})

    def test_invalid_scenario_is_rejected_without_contacting_upstream(self) -> None:
        # Given: a request with a scenario outside the finite protocol set.
        with patch.object(WebClientHandler, "_diagnose") as diagnose:
            # When: the request crosses the HTTP boundary.
            status_code, data = self.post_payload({"mode": "demo", "demo_scenario": "custom"})
        # Then: input is rejected before a TCP client is created.
        self.assertEqual(status_code, 400)
        self.assertEqual(data["code"], "invalid_scenario")
        diagnose.assert_not_called()

    def test_malformed_json_and_transfer_encoding_are_safe_bad_requests(self) -> None:
        # Given: malformed JSON and an unsupported chunked request.
        malformed = Request(
            f"http://127.0.0.1:{self.web_server.server_address[1]}/api/diagnose",
            data=b"{",
            headers={"Content-Type": "application/json"},
        )
        try:
            malformed_response = urlopen(malformed, timeout=3)
        except HTTPError as exc:
            malformed_response = exc
        with malformed_response:
            malformed_data = json.load(malformed_response)

        with closing(HTTPConnection("127.0.0.1", self.web_server.server_port, timeout=3)) as client:
            client.request(
                "POST",
                "/api/diagnose",
                body=b"4\r\n{}\r\n0\r\n\r\n",
                headers={"Transfer-Encoding": "chunked"},
                encode_chunked=False,
            )
            transfer_response = client.getresponse()
            transfer_data = json.loads(transfer_response.read())

        # Then: both responses use the stable bounded input error shape.
        self.assertEqual(malformed_response.status, 400)
        self.assertEqual(malformed_data["code"], "invalid_request")
        self.assertEqual(transfer_response.status, 400)
        self.assertEqual(transfer_data["code"], "invalid_request")

    def test_missing_and_negative_content_lengths_are_rejected(self) -> None:
        # Given: requests that cannot describe one bounded regular body.
        requests = (
            b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n",
            b"POST /api/diagnose HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\nConnection: close\r\n\r\n",
        )
        for raw_request in requests:
            with self.subTest(request=raw_request):
                # When: each request crosses the real loopback HTTP boundary.
                with socket.create_connection(("127.0.0.1", self.web_server.server_port), timeout=3) as client:
                    client.sendall(raw_request)
                    response = client.makefile("rb").read()

                # Then: it receives the stable input failure response.
                self.assertIn(b" 400 ", response.partition(b"\r\n")[0])
                self.assertIn(b'"code": "invalid_request"', response)

    def test_truncated_regular_body_returns_within_whole_body_deadline(self) -> None:
        # Given: a client declares a regular body but sends only one byte.
        started = time.monotonic()
        with socket.create_connection(("127.0.0.1", self.web_server.server_port), timeout=3) as client:
            client.sendall(
                b"POST /api/diagnose HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 10\r\n"
                b"Connection: close\r\n\r\n{"
            )
            response = client.makefile("rb").read()
        elapsed = time.monotonic() - started

        # Then: the whole-body deadline produces a safe 400 instead of hanging.
        self.assertIn(b" 400 ", response.partition(b"\r\n")[0])
        self.assertIn(b'"code": "invalid_request"', response)
        self.assertLess(elapsed, 2.8)

    def test_oversized_request_body_is_rejected(self) -> None:
        status_code, data = self.post_payload({"padding": "x" * 5000})
        self.assertEqual(status_code, 400)
        self.assertIn("4096", data["error"])

    def test_incomplete_oversized_upload_returns_an_error_without_hanging(self) -> None:
        # Given: a client declares an oversized body but does not send it.
        with closing(HTTPConnection("127.0.0.1", self.web_server.server_port, timeout=3)) as client:
            # When: the server receives only the request headers.
            client.request("POST", "/api/diagnose", headers={"Content-Length": "5000"})
            response = client.getresponse()
            data = json.loads(response.read())
        # Then: rejection still returns a complete JSON response within the client timeout.
        self.assertEqual(response.status, 400)
        self.assertIn("4096", data["error"])

    def test_upstream_failures_map_to_safe_http_statuses(self) -> None:
        # Given: each upstream failure class produced at the TCP boundary.
        cases = (
            (ConnectionResetError("secret reset detail"), 502, "upstream_failure"),
            (ProtocolError("secret protocol detail"), 502, "upstream_failure"),
            (TimeoutError("secret timeout detail"), 504, "upstream_timeout"),
            (RuntimeError("secret internal detail"), 500, "internal_error"),
        )
        for failure, expected_status, expected_code in cases:
            with self.subTest(code=expected_code), patch.object(
                WebClientHandler,
                "_diagnose",
                side_effect=failure,
            ):
                status_code, data = self.post_payload({"mode": "demo", "demo_scenario": "normal"})
            self.assertEqual(status_code, expected_status)
            self.assertEqual(data["code"], expected_code)
            self.assertIn("error", data)
            self.assertNotIn("secret", json.dumps(data))

    def test_tcp_connect_failures_return_observed_reason_without_raw_error(self) -> None:
        cases = (
            (ConnectionRefusedError(10061, "secret"), 502, "connection_refused"),
            (socket.gaierror(11001, "secret"), 502, "name_resolution"),
            (TimeoutError(10060, "secret"), 504, "timeout"),
        )
        for failure, expected_status, reason in cases:
            with self.subTest(reason=reason), patch.object(DiagnosticClient, "connect", side_effect=failure):
                status_code, data = self.post("demo")
            self.assertEqual(status_code, expected_status)
            self.assertEqual(data["reason_code"], reason)
            self.assertEqual(data["phase"], "connect")
            self.assertNotIn("secret", json.dumps(data))

    def test_diagnose_protocol_failure_identifies_stage(self) -> None:
        with patch.object(DiagnosticClient, "diagnose", side_effect=ProtocolError("secret")):
            status_code, data = self.post("demo")
        self.assertEqual(status_code, 502)
        self.assertEqual(data["reason_code"], "protocol_error")
        self.assertEqual(data["phase"], "diagnose")
        self.assertNotIn("secret", json.dumps(data))

    def test_nonfinite_upstream_payload_is_a_safe_protocol_failure(self) -> None:
        # Given: an upstream response that cannot be represented as valid JSON.
        invalid_response = {
            "type": "diagnostic_result",
            "result": {"mode": "DEMO", "status": "NORMAL", "detail": math.nan},
            "osi_trace": [],
        }
        # When: the response crosses the HTTP serialization boundary.
        with patch.object(WebClientHandler, "_diagnose", return_value=invalid_response):
            status_code, data = self.post_payload({"mode": "demo", "demo_scenario": "normal"})
        # Then: no nonstandard JSON or raw exception text escapes.
        self.assertEqual(status_code, 502)
        self.assertEqual(data["code"], "upstream_failure")
        self.assertNotIn("NaN", json.dumps(data))

    def test_metrics_scrape_has_no_diagnostic_side_effect(self) -> None:
        # Given: a running web process whose diagnostic adapter would fail if called.
        with patch.object(WebClientHandler, "_diagnose", side_effect=AssertionError("must not run")) as diagnose:
            # When: Prometheus scrapes the metrics endpoint.
            with urlopen(f"http://127.0.0.1:{self.web_server.server_port}/metrics", timeout=3) as response:
                metrics = response.read().decode("utf-8")
        # Then: scrape succeeds without touching TCP or DB paths.
        self.assertEqual(response.status, 200)
        self.assertIn("dbms_diagnostic_requests_total", metrics)
        diagnose.assert_not_called()

    def test_metrics_record_observed_modes_without_cross_mode_health(self) -> None:
        # Given: one DEMO success followed by an invalid-mode request.
        status_code, _ = self.post("demo", "normal")
        invalid_status, _ = self.post("invalid")
        self.assertEqual((status_code, invalid_status), (200, 400))

        # When: this web process is scraped.
        with urlopen(f"http://127.0.0.1:{self.web_server.server_port}/metrics", timeout=3) as response:
            metrics = response.read().decode("utf-8")

        # Then: finite request labels are counted and LIVE health remains unknown.
        self.assertIn('dbms_diagnostic_requests_total{mode="demo",outcome="success"} 1', metrics)
        self.assertIn('dbms_diagnostic_requests_total{mode="unknown",outcome="invalid_request"} 1', metrics)
        self.assertIn('dbms_diagnostic_last_success{mode="demo"} 1', metrics)
        self.assertNotIn('dbms_diagnostic_last_success{mode="live"}', metrics)

    def test_page_shows_the_tcp_client_server_path(self) -> None:
        with urlopen(f"http://127.0.0.1:{self.web_server.server_address[1]}/", timeout=3) as response:
            page = response.read().decode("utf-8")
        self.assertIn("웹 클라이언트", page)
        self.assertIn("진단 서버", page)
        self.assertIn("TCP 연결", page)
        self.assertIn("Wireshark 확인", page)
        self.assertIn("1–4계층 중점", page)
        self.assertIn('id="evidence"', page)
        self.assertIn('id="next-check"', page)
        self.assertIn("<h1>DBMS 네트워크 접속 진단</h1>", page)


class WebRequestBodyLimitTests(unittest.TestCase):
    def test_oversized_body_is_consumed_before_error_response(self) -> None:
        # Given: rejected bytes are still waiting in the handler's input stream.
        body = BytesIO(b"x" * 5000)
        handler = WebClientHandler.__new__(WebClientHandler)
        handler.path = "/api/diagnose"
        handler.headers = Message()
        handler.headers["Content-Length"] = "5000"
        handler.rfile = body
        with socket.socket() as connection, patch.object(handler, "_send_json") as send_json:
            handler.connection = connection
            # When: the handler rejects the oversized request.
            handler.do_POST()
        # Then: closing the connection cannot discard unread submitted request bytes.
        self.assertEqual(body.tell(), 5000)
        self.assertEqual(send_json.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
