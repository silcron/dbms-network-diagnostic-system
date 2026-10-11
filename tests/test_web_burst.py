from __future__ import annotations

import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from unittest.mock import patch

from app.web_client import DiagnosticWebServer, WebClientHandler


class DiagnosticWebServerBurstTests(unittest.TestCase):
    def test_ten_pending_requests_succeed_and_update_metrics_exactly(self) -> None:
        # Given: ten clients released while the production listener has not accepted a request yet.
        server = DiagnosticWebServer(("127.0.0.1", 0), WebClientHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        start = threading.Barrier(11)
        payload = json.dumps({"mode": "demo", "demo_scenario": "normal"}).encode()
        diagnostic = {
            "type": "diagnostic_result",
            "checked_at": "legacy timestamp",
            "client_rtt_ms": 0.1,
            "result": {
                "mode": "DEMO",
                "scenario": "normal",
                "status": "NORMAL",
                "database": "Legacy database label",
                "detail": "Legacy diagnostic detail",
                "error_kind": "none",
                "total_ms": 0.0,
            },
            "osi_trace": [
                {"layer": f"Layer {number}", "application": f"Legacy role {number}"}
                for number in range(7, 0, -1)
            ],
        }

        def post(_: int) -> int:
            start.wait(timeout=3)
            request = Request(
                f"http://127.0.0.1:{server.server_port}/api/diagnose",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=5) as response:
                response.read()
                return response.status

        try:
            # When: every client connects before the accept loop begins draining the bounded backlog.
            with patch.object(WebClientHandler, "_diagnose", return_value=diagnostic), ThreadPoolExecutor(
                max_workers=10
            ) as executor:
                futures = [executor.submit(post, index) for index in range(10)]
                start.wait(timeout=3)
                time.sleep(0.1)
                server_thread.start()
                statuses = [future.result(timeout=5) for future in futures]
            with urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=3) as response:
                metrics = response.read().decode()
        finally:
            if server_thread.is_alive():
                server.shutdown()
            server.server_close()
            if server_thread.ident is not None:
                server_thread.join(timeout=2)

        # Then: no connection is refused and every accepted result contributes exactly once.
        self.assertEqual(statuses, [200] * 10)
        self.assertIn('dbms_diagnostic_requests_total{mode="demo",outcome="success"} 10', metrics)
        self.assertIn(
            'dbms_diagnostic_results_total{mode="demo",status="NORMAL",error_kind="none"} 10',
            metrics,
        )
        self.assertIn('dbms_diagnostic_duration_seconds_count{mode="demo"} 10', metrics)


if __name__ == "__main__":
    unittest.main()
