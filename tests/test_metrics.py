from __future__ import annotations

import math
import threading
import unittest

from app.metrics import DiagnosticObservation, InvalidMetricValue, WebMetrics


class WebMetricsTests(unittest.TestCase):
    def test_concurrent_observations_are_counted_exactly(self) -> None:
        # Given: one process-local metrics accumulator and concurrent workers.
        metrics = WebMetrics()
        observation = DiagnosticObservation(
            mode="live",
            status="NORMAL",
            error_kind="none",
            total_ms=250.0,
        )

        # When: every worker records the same request and diagnostic result.
        def record() -> None:
            for _ in range(100):
                metrics.observe_request("live", "success")
                metrics.observe_result(observation)

        threads = [threading.Thread(target=record) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        # Then: no update is lost and the duration aggregate uses seconds.
        rendered = metrics.render()
        self.assertIn('dbms_diagnostic_requests_total{mode="live",outcome="success"} 800', rendered)
        self.assertIn(
            'dbms_diagnostic_results_total{mode="live",status="NORMAL",error_kind="none"} 800',
            rendered,
        )
        self.assertIn('dbms_diagnostic_duration_seconds_count{mode="live"} 800', rendered)
        self.assertIn('dbms_diagnostic_duration_seconds_sum{mode="live"} 200', rendered)

    def test_health_is_omitted_until_observed_and_modes_are_isolated(self) -> None:
        # Given: a fresh web process with no diagnostic observations.
        metrics = WebMetrics()

        # When: metrics are scraped before and after distinct LIVE and DEMO results.
        initial = metrics.render()
        metrics.observe_result(DiagnosticObservation("live", "DOWN", "network", None))
        metrics.observe_result(DiagnosticObservation("demo", "NORMAL", "none", None))
        observed = metrics.render()

        # Then: unknown health is absent and DEMO cannot overwrite LIVE health.
        self.assertNotIn("dbms_diagnostic_last_success{", initial)
        self.assertIn('dbms_diagnostic_last_success{mode="live"} 0', observed)
        self.assertIn('dbms_diagnostic_last_success{mode="demo"} 1', observed)

    def test_arbitrary_labels_and_nonfinite_durations_are_rejected(self) -> None:
        # Given: the finite label and duration contract.
        metrics = WebMetrics()

        # When / Then: attacker-controlled labels and nonfinite numbers are refused.
        with self.assertRaises(InvalidMetricValue):
            metrics.observe_request("session-123", "success")
        with self.assertRaises(InvalidMetricValue):
            metrics.observe_request("live", "surprise")
        for value in (math.nan, math.inf, -1.0):
            with self.subTest(value=value), self.assertRaises(InvalidMetricValue):
                DiagnosticObservation("live", "NORMAL", "none", value)
        self.assertNotIn("session-123", metrics.render())

    def test_rendered_samples_are_finite(self) -> None:
        # Given: valid result durations from both supported modes.
        metrics = WebMetrics()
        metrics.observe_result(DiagnosticObservation("live", "WARNING", "none", 0.0))
        metrics.observe_result(DiagnosticObservation("demo", "DOWN", "network", 12.5))

        # When: Prometheus exposition is rendered.
        samples = [line for line in metrics.render().splitlines() if line and not line.startswith("#")]

        # Then: every emitted sample parses as a finite number.
        for sample in samples:
            value = float(sample.rsplit(" ", 1)[1])
            self.assertTrue(math.isfinite(value), sample)


if __name__ == "__main__":
    unittest.main()
