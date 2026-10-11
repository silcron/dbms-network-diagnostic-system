from __future__ import annotations

import importlib.util
import json
import math
import os
import socket
import sys
import unittest
from types import SimpleNamespace
from typing import Mapping, Union
from unittest.mock import MagicMock, patch

from app.diagnostics import build_osi_trace, probe_database


JsonScalar = Union[str, int, float, None]
LivePayload = Mapping[str, JsonScalar]

BASE_LIVE_ENV = {
    "DB_HOST": "127.0.0.1",
    "DB_PORT": "3306",
    "DB_USER": "diagnostic-user",
    "DB_PASSWORD": "diagnostic-password",
    "DB_NAME": "diagnostic-db",
    "DB_CONNECT_TIMEOUT_SECONDS": "3",
    "DB_READ_TIMEOUT_SECONDS": "3",
    "DB_WRITE_TIMEOUT_SECONDS": "3",
    "DB_WARNING_MS": "500",
}


class FakeDatabaseError(Exception):
    pass


class LiveDiagnosticTests(unittest.TestCase):
    def assert_live_contract(self, result: LivePayload) -> None:
        self.assertEqual(result["mode"], "LIVE")
        self.assertIn(result["failed_phase"], {None, "config", "dependency", "connect", "query"})
        self.assertIn(
            result["error_kind"],
            {"none", "config", "dependency", "network", "timeout", "auth", "database", "query", "internal"},
        )
        errno = result["db_errno"]
        self.assertTrue(errno is None or (isinstance(errno, int) and not isinstance(errno, bool)))
        query_ms = result["query_ms"]
        self.assertTrue(query_ms is None or (isinstance(query_ms, float) and math.isfinite(query_ms) and query_ms >= 0))
        for key in ("total_ms", "database_rtt_ms"):
            duration = result[key]
            self.assertIsInstance(duration, float)
            self.assertTrue(math.isfinite(duration))
            self.assertGreaterEqual(duration, 0)

    @staticmethod
    def configured_connection(query_result: tuple[int, ...] = (1,)) -> tuple[MagicMock, MagicMock]:
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = query_result
        return connection, cursor

    def test_osi_trace_uses_korean_and_describes_loopback_accurately(self) -> None:
        # Given/When: a loopback OSI trace is built.
        trace = build_osi_trace("127.0.0.1", "127.0.0.1", 9090, "sample-session")

        # Then: all seven layers describe the real framing and loopback path.
        self.assertEqual(len(trace), 7)
        self.assertEqual(trace[0]["layer"], "7 응용 계층")
        self.assertIn("4바이트 길이 헤더", trace[0]["application"])
        self.assertNotIn("4바이트", trace[3]["application"])
        self.assertIn("TCP", trace[3]["application"])
        self.assertIn("루프백", trace[5]["application"])
        self.assertIn("실제 전송은 없습니다", trace[6]["application"])

    def test_demo_preserves_three_explicit_statuses_without_live_fields(self) -> None:
        # Given: LIVE configuration exists but DEMO mode is explicitly selected.
        fake_driver = SimpleNamespace(connect=MagicMock())
        with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
            # When: each supported demonstration scenario is requested.
            results = {scenario: probe_database(scenario, mode="demo") for scenario in ("normal", "warning", "down")}

        # Then: statuses stay simulated and LIVE-only fields remain absent.
        self.assertEqual({key: value["status"] for key, value in results.items()}, {"normal": "NORMAL", "warning": "WARNING", "down": "DOWN"})
        for result in results.values():
            self.assertEqual(result["mode"], "DEMO")
            self.assertIn("모의 진단", result["detail"])
            for key in ("failed_phase", "error_kind", "db_errno", "query_ms", "total_ms"):
                self.assertNotIn(key, result)
        fake_driver.connect.assert_not_called()

    def test_config_failure_when_host_port_or_budget_is_invalid(self) -> None:
        # Given: each case contains one invalid LIVE setting.
        cases = (
            ("DB_HOST", None),
            ("DB_HOST", " "),
            ("DB_PORT", "not-a-port"),
            ("DB_PORT", "0"),
            ("DB_PORT", "65536"),
            ("DB_CONNECT_TIMEOUT_SECONDS", "nan"),
            ("DB_READ_TIMEOUT_SECONDS", "0"),
            ("DB_WRITE_TIMEOUT_SECONDS", "31"),
            ("DB_WARNING_MS", "inf"),
            ("DB_WARNING_MS", "0"),
        )
        for setting, value in cases:
            with self.subTest(setting=setting, value=value):
                env = dict(BASE_LIVE_ENV)
                if value is None:
                    env.pop(setting)
                else:
                    env[setting] = value
                fake_driver = SimpleNamespace(connect=MagicMock())

                # When: a LIVE diagnosis parses its environment boundary.
                with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
                    result = probe_database(mode="live")

                # Then: configuration fails safely before the driver is called.
                self.assertEqual(result["status"], "WARNING")
                self.assertEqual(result["failed_phase"], "config")
                self.assertEqual(result["error_kind"], "config")
                self.assertIsNone(result["db_errno"])
                self.assertIsNone(result["query_ms"])
                self.assert_live_contract(result)
                fake_driver.connect.assert_not_called()

    def test_dependency_failure_when_pymysql_is_unavailable(self) -> None:
        # Given: LIVE settings are valid and PyMySQL cannot be imported.
        with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": None}):
            # When: a LIVE diagnosis starts.
            result = probe_database(mode="live")

        # Then: the result names the dependency phase without faking LIVE success.
        self.assertEqual(result["status"], "WARNING")
        self.assertEqual(result["failed_phase"], "dependency")
        self.assertEqual(result["error_kind"], "dependency")
        self.assertIsNone(result["query_ms"])
        self.assert_live_contract(result)

    def test_success_uses_validated_budgets_and_reports_phase_timings(self) -> None:
        # Given: a driver returns SELECT 1 and distinct timeout budgets are configured.
        connection, cursor = self.configured_connection()
        fake_driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        env = dict(BASE_LIVE_ENV)
        env.update({"DB_CONNECT_TIMEOUT_SECONDS": "30", "DB_READ_TIMEOUT_SECONDS": "2.5", "DB_WRITE_TIMEOUT_SECONDS": "3.5"})

        # When: the LIVE diagnosis succeeds.
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
            result = probe_database("down", mode="live")

        # Then: the real connect/query path and additive contract are observable.
        self.assertEqual(result["status"], "NORMAL")
        self.assertIsNone(result["failed_phase"])
        self.assertEqual(result["error_kind"], "none")
        self.assertIsNone(result["db_errno"])
        self.assertIsInstance(result["database_connect_ms"], float)
        self.assertIsInstance(result["query_ms"], float)
        cursor.execute.assert_called_once_with("SELECT 1")
        connection.close.assert_called_once()
        fake_driver.connect.assert_called_once_with(
            host="127.0.0.1",
            port=3306,
            user="diagnostic-user",
            password="diagnostic-password",
            database="diagnostic-db",
            connect_timeout=30.0,
            read_timeout=2.5,
            write_timeout=3.5,
        )
        self.assert_live_contract(result)

    def test_warning_when_successful_total_exceeds_threshold(self) -> None:
        # Given: connect takes 100 ms, SELECT 1 takes 600 ms, and the warning threshold is 500 ms.
        connection, _ = self.configured_connection()
        fake_driver = SimpleNamespace(connect=MagicMock(return_value=connection))
        ticks = [0.0, 0.1, 0.2, 0.2, 0.8, 0.8]

        # When: the LIVE diagnosis completes successfully but slowly.
        with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}), patch(
            "app.db_probe.time.perf_counter", side_effect=ticks
        ):
            result = probe_database(mode="live")

        # Then: slowness is WARNING without inventing a failed phase or cause.
        self.assertEqual(result["status"], "WARNING")
        self.assertIsNone(result["failed_phase"])
        self.assertEqual(result["error_kind"], "none")
        self.assertEqual(result["database_connect_ms"], 100.0)
        self.assertEqual(result["query_ms"], 600.0)
        self.assertEqual(result["total_ms"], 800.0)
        timeout_arguments = fake_driver.connect.call_args.kwargs
        self.assertEqual(
            (
                timeout_arguments["connect_timeout"],
                timeout_arguments["read_timeout"],
                timeout_arguments["write_timeout"],
            ),
            (3.0, 3.0, 3.0),
        )
        self.assert_live_contract(result)

    def test_connect_errors_are_classified_without_raw_driver_text(self) -> None:
        # Given: representative auth, database, network, DNS, and timeout failures.
        wrapped_timeout = FakeDatabaseError(2003, "Can't connect to MySQL server")
        wrapped_timeout.__cause__ = TimeoutError("timed out")
        cases = (
            (FakeDatabaseError(1045, "raw secret auth timeout text"), "auth", "WARNING", 1045),
            (FakeDatabaseError(1049, "raw secret database timeout text"), "database", "WARNING", 1049),
            (FakeDatabaseError(2002, "raw secret network text"), "network", "DOWN", 2002),
            (FakeDatabaseError(2003, "Can't connect to MySQL server on 'timeout-db.local' (111)"), "network", "DOWN", 2003),
            (FakeDatabaseError(2003, "Can't connect to MySQL server on 'db.local' (timed out)"), "timeout", "DOWN", 2003),
            (wrapped_timeout, "timeout", "DOWN", 2003),
            (FakeDatabaseError(2005, "raw secret network text"), "network", "DOWN", 2005),
            (FakeDatabaseError(2006, "raw secret network text"), "network", "DOWN", 2006),
            (FakeDatabaseError(2013, "raw secret network text"), "network", "DOWN", 2013),
            (FakeDatabaseError(2013, "raw secret (timed out)"), "timeout", "DOWN", 2013),
            (socket.gaierror("raw secret DNS text"), "network", "DOWN", None),
            (TimeoutError("raw secret timeout text"), "timeout", "DOWN", None),
            (RuntimeError("raw secret internal text"), "internal", "WARNING", None),
        )
        for error, error_kind, status, errno in cases:
            with self.subTest(error_kind=error_kind):
                fake_driver = SimpleNamespace(connect=MagicMock(side_effect=error))

                # When: connect/auth raises the driver or OS error.
                with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
                    result = probe_database(mode="live")

                # Then: only stable phase/cause/errno data crosses the boundary.
                self.assertEqual(result["failed_phase"], "connect")
                self.assertEqual(result["error_kind"], error_kind)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["db_errno"], errno)
                self.assertIsNone(result["query_ms"])
                serialized = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("raw secret", serialized)
                self.assertNotIn("diagnostic-password", serialized)
                self.assert_live_contract(result)

    def test_query_errors_keep_query_phase_and_close_connection(self) -> None:
        # Given: SELECT 1 can fail with a query error, lost connection, or timeout.
        cases = (
            (FakeDatabaseError(1064, "raw query text"), "query", "WARNING", 1064),
            (FakeDatabaseError(2013, "raw lost connection text"), "network", "DOWN", 2013),
            (TimeoutError("raw timeout text"), "timeout", "DOWN", None),
            (RuntimeError("raw internal text"), "internal", "WARNING", None),
        )
        for error, error_kind, status, errno in cases:
            with self.subTest(error_kind=error_kind):
                connection, cursor = self.configured_connection()
                cursor.execute.side_effect = error
                fake_driver = SimpleNamespace(connect=MagicMock(return_value=connection))

                # When: the query phase fails.
                with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
                    result = probe_database(mode="live")

                # Then: the phase stays query while the underlying cause controls status.
                self.assertEqual(result["failed_phase"], "query")
                self.assertEqual(result["error_kind"], error_kind)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["db_errno"], errno)
                self.assertIsInstance(result["query_ms"], float)
                connection.close.assert_called_once()
                self.assert_live_contract(result)

    def test_unexpected_query_result_and_close_failure_are_safe(self) -> None:
        # Given: SELECT 1 returns the wrong value and cleanup itself raises.
        connection, _ = self.configured_connection((2,))
        connection.close.side_effect = RuntimeError("raw cleanup text")
        fake_driver = SimpleNamespace(connect=MagicMock(return_value=connection))

        # When: the LIVE diagnosis handles the invalid query result.
        with patch.dict(os.environ, BASE_LIVE_ENV, clear=True), patch.dict(sys.modules, {"pymysql": fake_driver}):
            result = probe_database(mode="live")

        # Then: cleanup cannot replace the safe query diagnosis.
        self.assertEqual(result["status"], "WARNING")
        self.assertEqual(result["failed_phase"], "query")
        self.assertEqual(result["error_kind"], "query")
        self.assertIsNone(result["db_errno"])
        self.assertNotIn("raw cleanup", json.dumps(result, ensure_ascii=False))
        connection.close.assert_called_once()
        self.assert_live_contract(result)

    def test_real_refused_loopback_port_is_live_network_down(self) -> None:
        # Given: PyMySQL is installed and a released loopback port has no listener.
        if importlib.util.find_spec("pymysql") is None:
            self.skipTest("PyMySQL is required for the real refused-port scenario")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind(("127.0.0.1", 0))
            refused_port = reservation.getsockname()[1]
        env = dict(BASE_LIVE_ENV)
        env.update({"DB_PORT": str(refused_port), "DB_CONNECT_TIMEOUT_SECONDS": "0.2"})

        # When: the production PyMySQL connect path reaches that port.
        with patch.dict(os.environ, env, clear=True):
            result = probe_database(mode="live")

        # Then: the public LIVE surface reports a bounded network failure.
        self.assertEqual(result["status"], "DOWN")
        self.assertEqual(result["failed_phase"], "connect")
        self.assertIn(result["error_kind"], {"network", "timeout"})
        self.assertIsNone(result["query_ms"])
        self.assert_live_contract(result)


if __name__ == "__main__":
    unittest.main()
