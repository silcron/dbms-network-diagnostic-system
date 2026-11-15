from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.client import format_diagnostic_lines
from scripts import check_presentation_demo


class DiagnosticPresentationTests(unittest.TestCase):
    def test_desktop_shows_actions_for_non_primary_targets(self) -> None:
        base = {"mode": "LIVE", "database": "MariaDB", "detail": "점검 결과"}
        auth = {**base, "target_id": 2, "status": "WARNING", "error_kind": "auth"}
        down = {**base, "target_id": 3, "status": "DOWN", "error_kind": "network"}
        lines = format_diagnostic_lines({"result": down, "results": [auth, down], "osi_trace": [], "client_rtt_ms": 1})
        rendered = "\n".join(lines)
        self.assertIn("우선 점검: DB 3", rendered)
        self.assertIn("DB2_USER", rendered)
        self.assertIn("주소·포트", rendered)

    def test_optional_live_diagnostic_fields_are_rendered(self) -> None:
        # Given: a LIVE result with the full additive diagnosis contract.
        response = {
            "client_rtt_ms": 7.5,
            "result": {
                "status": "DOWN",
                "mode": "LIVE",
                "database": "MariaDB (127.0.0.1)",
                "detail": "연결 실패",
                "database_connect_ms": 3.0,
                "query_ms": 2.0,
                "total_ms": 6.0,
                "failed_phase": "query",
                "error_kind": "query",
                "db_errno": 2013,
            },
            "osi_trace": [],
        }

        # When: the desktop presentation formats the result.
        rendered = "\n".join(format_diagnostic_lines(response))

        # Then: every valid optional fact is present with truthful labels.
        self.assertIn("DB 연결·인증: 3.0 ms", rendered)
        self.assertIn("SELECT 1: 2.0 ms", rendered)
        self.assertIn("진단 전체: 6.0 ms", rendered)
        self.assertIn("실패 단계: SELECT 1", rendered)
        self.assertIn("오류 원인: 쿼리", rendered)
        self.assertIn("DB 오류 번호: 2013", rendered)

    def test_old_payload_omits_missing_optional_fields(self) -> None:
        # Given: a compatible older response with no additive fields.
        response = {
            "client_rtt_ms": 1.0,
            "result": {
                "status": "NORMAL",
                "mode": "DEMO",
                "database": "MariaDB (모의 시연)",
                "detail": "모의 결과",
            },
            "osi_trace": [],
        }

        # When: the desktop presentation formats it.
        rendered = "\n".join(format_diagnostic_lines(response))

        # Then: absent data is omitted instead of displayed as zero.
        self.assertNotIn("진단 세부", rendered)
        self.assertNotIn("DB 연결·인증", rendered)
        self.assertNotIn("진단 전체", rendered)

    def test_demo_timings_are_explicitly_simulated(self) -> None:
        # Given: a DEMO payload that explicitly carries a timing value.
        response = {
            "client_rtt_ms": 1.0,
            "result": {
                "status": "WARNING",
                "mode": "DEMO",
                "database": "MariaDB (모의 시연)",
                "detail": "모의 결과",
                "total_ms": 800.0,
                "db_errno": 2003,
            },
            "osi_trace": [],
        }

        # When: the value is presented.
        rendered = "\n".join(format_diagnostic_lines(response))

        # Then: it cannot be mistaken for a LIVE measurement.
        self.assertIn("모의 진단 전체: 800.0 ms", rendered)
        self.assertIn("모의 DB 오류 번호: 2003", rendered)


class PresentationGateTests(unittest.TestCase):
    def test_demo_only_skips_live_but_checks_all_demo_statuses(self) -> None:
        # Given: an available web page and the demo-only command-line gate.
        page = MagicMock()
        page.__enter__.return_value.read.return_value = "통신 경로 OSI 7계층".encode()
        argv = ["check_presentation_demo", "--demo-only"]

        # When: the presentation gate runs.
        with patch("sys.argv", argv), patch.object(check_presentation_demo, "urlopen", return_value=page), patch.object(
            check_presentation_demo,
            "check_tcp",
        ) as check_tcp:
            check_presentation_demo.main()

        # Then: the three single-DB scenarios and the mixed batch all run.
        self.assertEqual(
            [call.args[2:5] for call in check_tcp.call_args_list],
            [
                ("demo", "normal", "NORMAL"),
                ("demo", "warning", "WARNING"),
                ("demo", "down", "DOWN"),
                ("demo", "mixed", "DOWN"),
            ],
        )

    def test_mixed_gate_rejects_single_down_masquerading_as_a_batch(self) -> None:
        down = {"mode": "DEMO", "status": "DOWN", "target_id": 3, "scenario": "down"}
        with patch.object(check_presentation_demo, "DiagnosticClient") as factory:
            client = factory.return_value
            client.diagnose.return_value = {
                "type": "diagnostic_result", "result": down, "results": [down], "osi_trace": [{}] * 7,
            }
            with self.assertRaises(check_presentation_demo.PresentationCheckError):
                check_presentation_demo.check_tcp("localhost", 9090, "demo", "mixed", "DOWN")
            client.logout.assert_called_once()


if __name__ == "__main__":
    unittest.main()
