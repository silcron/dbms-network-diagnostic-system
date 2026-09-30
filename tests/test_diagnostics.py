from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.diagnostics import build_osi_trace, probe_database


class LiveDiagnosticTests(unittest.TestCase):
    def test_osi_trace_uses_korean_and_describes_loopback_accurately(self) -> None:
        trace = build_osi_trace("127.0.0.1", "127.0.0.1", 9090, "sample-session")
        self.assertEqual(len(trace), 7)
        self.assertEqual(trace[0]["layer"], "7 응용 계층")
        self.assertIn("4바이트 길이 헤더", trace[0]["application"])
        self.assertNotIn("4바이트", trace[3]["application"])
        self.assertIn("TCP", trace[3]["application"])
        self.assertIn("루프백", trace[5]["application"])
        self.assertIn("실제 전송은 없습니다", trace[6]["application"])

    def test_unreachable_db_port_is_down(self) -> None:
        fake_pymysql = SimpleNamespace(connect=MagicMock(side_effect=ConnectionRefusedError))
        with patch.dict(os.environ, {"DB_HOST": "127.0.0.1", "DB_PORT": "3306"}):
            with patch.dict(sys.modules, {"pymysql": fake_pymysql}):
                result = probe_database("normal")
        self.assertEqual(result["mode"], "LIVE")
        self.assertEqual(result["status"], "DOWN")
        self.assertIn("네트워크 연결 실패", result["detail"])

    def test_reachable_db_and_select_one_are_normal(self) -> None:
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
        fake_pymysql = SimpleNamespace(connect=MagicMock(return_value=connection))
        with patch.dict(os.environ, {"DB_HOST": "127.0.0.1", "DB_PORT": "3306"}):
            with patch.dict(sys.modules, {"pymysql": fake_pymysql}):
                result = probe_database("down")
        self.assertEqual(result["mode"], "LIVE")
        self.assertEqual(result["status"], "NORMAL")
        self.assertIn("database_connect_ms", result)
        self.assertIn("database_rtt_ms", result)
        connection.close.assert_called_once()
        fake_pymysql.connect.assert_called_once()

    def test_demo_is_explicit_even_when_live_database_is_configured(self) -> None:
        fake_pymysql = SimpleNamespace(connect=MagicMock())
        with patch.dict(os.environ, {"DB_HOST": "127.0.0.1"}):
            with patch.dict(sys.modules, {"pymysql": fake_pymysql}):
                result = probe_database("down", mode="demo")
        self.assertEqual(result["mode"], "DEMO")
        self.assertEqual(result["status"], "DOWN")
        self.assertIn("모의 진단", result["detail"])
        fake_pymysql.connect.assert_not_called()

    def test_live_without_configuration_does_not_fall_back_to_demo(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            result = probe_database("normal", mode="live")
        self.assertEqual(result["mode"], "LIVE")
        self.assertEqual(result["status"], "WARNING")
        self.assertIn("DB_HOST", result["detail"])


if __name__ == "__main__":
    unittest.main()
