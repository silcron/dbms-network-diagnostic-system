from __future__ import annotations

import json
import math
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from app.web_client import WebClientHandler
from app.web_inputs import JsonObject, JsonValue


class WebResponseBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), WebClientHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    @staticmethod
    def legacy_response() -> JsonObject:
        return {
            "type": "diagnostic_result",
            "checked_at": "legacy timestamp",
            "client_rtt_ms": 1.25,
            "result": {
                "mode": "DEMO",
                "scenario": "normal",
                "status": "NORMAL",
                "database": "Legacy database label",
                "detail": "Legacy diagnostic detail",
            },
            "osi_trace": [
                {"layer": f"Layer {number}", "application": f"Legacy role {number}"}
                for number in range(7, 0, -1)
            ],
        }

    @staticmethod
    def legacy_trace() -> list[JsonValue]:
        return [
            {"layer": f"Layer {number}", "application": f"Legacy role {number}"}
            for number in range(7, 0, -1)
        ]

    @staticmethod
    def result_object(response: JsonObject) -> JsonObject:
        result = response.get("result")
        if not isinstance(result, dict):
            raise AssertionError("test fixture has no result object")
        return result

    def without_top_level_field(self, field: str) -> JsonObject:
        response = self.legacy_response()
        del response[field]
        return response

    def with_top_level_field(self, field: str, value: JsonValue) -> JsonObject:
        response = self.legacy_response()
        response[field] = value
        return response

    def without_result_field(self, field: str) -> JsonObject:
        response = self.legacy_response()
        del self.result_object(response)[field]
        return response

    def with_result_field(self, field: str, value: JsonValue) -> JsonObject:
        response = self.legacy_response()
        self.result_object(response)[field] = value
        return response

    def post(self, response: JsonObject) -> tuple[int, JsonObject]:
        request = Request(
            f"http://127.0.0.1:{self.server.server_port}/api/diagnose",
            data=json.dumps({"mode": "demo", "demo_scenario": "normal"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with patch.object(WebClientHandler, "_diagnose", return_value=response):
            try:
                http_response = urlopen(request, timeout=3)
            except HTTPError as exc:
                http_response = exc
            with http_response:
                return http_response.status, json.load(http_response)

    def scrape_metrics(self) -> str:
        with urlopen(f"http://127.0.0.1:{self.server.server_port}/metrics", timeout=3) as response:
            return response.read().decode("utf-8")

    def test_complete_legacy_response_remains_compatible(self) -> None:
        # Given: an older response with every established UI field and no new diagnostic facts.
        legacy_response = self.legacy_response()

        # When: it crosses the real loopback HTTP boundary.
        status_code, data = self.post(legacy_response)

        # Then: the complete older shape remains a successful response.
        self.assertEqual(status_code, 200)
        self.assertEqual(data, legacy_response)

    def test_malformed_upstream_shapes_are_502_without_success_metrics(self) -> None:
        # Given: upstream results missing or corrupting fields consumed by the established browser UI.
        invalid_responses: dict[str, JsonObject] = {
            "sparse_result": {
                "type": "diagnostic_result",
                "result": {"mode": "DEMO", "status": "NORMAL", "error_kind": "none"},
            },
            "missing_checked_at": self.without_top_level_field("checked_at"),
            "non_string_checked_at": self.with_top_level_field("checked_at", 1),
            "missing_client_rtt": self.without_top_level_field("client_rtt_ms"),
            "string_client_rtt": self.with_top_level_field("client_rtt_ms", "1.25"),
            "negative_client_rtt": self.with_top_level_field("client_rtt_ms", -0.01),
            "nonfinite_client_rtt": self.with_top_level_field("client_rtt_ms", math.inf),
            "missing_scenario": self.without_result_field("scenario"),
            "non_string_scenario": self.with_result_field("scenario", 1),
            "missing_database": self.without_result_field("database"),
            "non_string_database": self.with_result_field("database", 1),
            "missing_detail": self.without_result_field("detail"),
            "non_string_detail": self.with_result_field("detail", 1),
            "missing_trace": self.without_top_level_field("osi_trace"),
            "empty_trace": self.with_top_level_field("osi_trace", []),
            "six_layers": self.with_top_level_field("osi_trace", self.legacy_trace()[:6]),
            "eight_layers": self.with_top_level_field(
                "osi_trace",
                [*self.legacy_trace(), {"layer": "Extra", "application": "Extra role"}],
            ),
            "non_list_trace": self.with_top_level_field("osi_trace", {}),
            "missing_layer": self.with_top_level_field(
                "osi_trace",
                [{"application": "Legacy role 7"}, *self.legacy_trace()[1:]],
            ),
            "blank_layer": self.with_top_level_field(
                "osi_trace",
                [{"layer": " ", "application": "Legacy role 7"}, *self.legacy_trace()[1:]],
            ),
            "missing_application": self.with_top_level_field(
                "osi_trace",
                [{"layer": "Layer 7"}, *self.legacy_trace()[1:]],
            ),
            "blank_application": self.with_top_level_field(
                "osi_trace",
                [{"layer": "Layer 7", "application": " "}, *self.legacy_trace()[1:]],
            ),
        }

        # When: each malformed response crosses the real loopback HTTP boundary.
        for name, invalid_response in invalid_responses.items():
            with self.subTest(name=name):
                status_code, data = self.post(invalid_response)
                self.assertEqual(status_code, 502)
                self.assertEqual(data["code"], "upstream_failure")
                self.assertEqual(data["message"], "diagnostic server communication failed")
        metrics = self.scrape_metrics()

        # Then: failures are counted without recording a successful request or diagnostic result.
        self.assertIn(
            f'dbms_diagnostic_requests_total{{mode="demo",outcome="upstream_failure"}} {len(invalid_responses)}',
            metrics,
        )
        self.assertNotIn('outcome="success"', metrics)
        self.assertNotIn("dbms_diagnostic_results_total{", metrics)
        self.assertNotIn("dbms_diagnostic_last_success{", metrics)


if __name__ == "__main__":
    unittest.main()
