from __future__ import annotations

import unittest

from app.feedback import batch_summary, target_feedback
from app.protocol import JsonObject


class FeedbackTests(unittest.TestCase):
    def test_warning_uses_the_observed_cause(self) -> None:
        slow: JsonObject = {"mode": "LIVE", "status": "WARNING", "error_kind": "none", "failed_phase": None}
        auth: JsonObject = {**slow, "error_kind": "auth", "failed_phase": "connect", "target_id": 2}
        sparse: JsonObject = {"mode": "LIVE", "status": "WARNING"}
        self.assertIn("지연", target_feedback(slow).finding)
        self.assertIn("DB2_USER", target_feedback(auth).next_check)
        self.assertNotIn("지연", target_feedback(sparse).finding)

    def test_query_timeout_does_not_suggest_login_failed(self) -> None:
        target: JsonObject = {"status": "DOWN", "error_kind": "timeout", "failed_phase": "query", "mode": "LIVE"}
        self.assertIn("SELECT 1", target_feedback(target).finding)
        self.assertIn("DB_READ_TIMEOUT_SECONDS", target_feedback(target).next_check)

    def test_plugin_failure_on_an_older_server_does_not_blame_the_database_name(self) -> None:
        target: JsonObject = {"mode": "LIVE", "error_kind": "database", "failed_phase": "connect", "db_errno": 2059}
        self.assertIn("인증 플러그인", target_feedback(target).finding)
        self.assertIn("PyMySQL", target_feedback(target).next_check)

    def test_mixed_and_tied_targets_keep_their_real_slot_numbers(self) -> None:
        targets: list[JsonObject] = [{"target_id": 2, "status": "WARNING"}, {"target_id": 3, "status": "DOWN"}]
        text = batch_summary(targets)
        self.assertLess(text.index("DB 3"), text.index("DB 2"))
        targets[0]["status"] = "DOWN"
        self.assertIn("DB 2, DB 3", batch_summary(targets))
        for target in targets:
            target["status"] = "NORMAL"
        self.assertNotIn("우선", batch_summary(targets))
        self.assertIn("2개", batch_summary(targets))

    def test_simulation_keeps_the_example_action_separate_from_real_measurements(self) -> None:
        target: JsonObject = {"mode": "DEMO", "status": "WARNING", "scenario": "warning"}
        self.assertIn("모의", target_feedback(target).finding)
        self.assertIn("지연", target_feedback(target).next_check)
        self.assertIn("LIVE", target_feedback(target).next_check)
