from __future__ import annotations

import json
import io
import os
import socket
import subprocess
import threading
import unittest
from contextlib import redirect_stderr
from typing import Union
from unittest.mock import patch

from app.server import DiagnosticHandler, DiagnosticServer
from fault_contract_server import ContractState, RunningContractServer
from scripts.check_fault_scenarios import (
    DatabaseProbe,
    DiagnosticProbe,
    Endpoint,
    FailureCause,
    FailureObservation,
    FaultAssertionError,
    ResultLabel,
    ResultObservation,
    RunnerConfig,
    main,
    run_fault_suite,
)
from scripts.toxiproxy_adapter import (
    OwnedProxy,
    ProxyCollisionError,
    ProxyDefinition,
    ToxicDefinition,
    ToxiproxyClient,
    ToxiproxyContractError,
)


class PlannedFailure(RuntimeError):
    pass


def healthy(elapsed: float = 0.02) -> ResultObservation:
    return ResultObservation(
        label=ResultLabel(mode="DEMO", status="NORMAL", scenario="normal"),
        elapsed_seconds=elapsed,
        error_kind="none",
        failed_phase=None,
        total_ms=None,
        query_ms=None,
        integrity_verified=True,
    )


class ScriptedProbe:
    def __init__(self, observations: list[Union[ResultObservation, FailureObservation]]) -> None:
        self.observations = list(observations)
        self.timeouts: list[float] = []

    def __call__(self, timeout_seconds: float) -> Union[ResultObservation, FailureObservation]:
        self.timeouts.append(timeout_seconds)
        if not self.observations:
            raise AssertionError("probe called more often than expected")
        return self.observations.pop(0)


def config(name: str) -> RunnerConfig:
    return RunnerConfig(
        proxy=ProxyDefinition(name=name, listen="127.0.0.1:19091", upstream="127.0.0.1:9090"),
        listen=Endpoint(host="127.0.0.1", port=19091),
        target="diagnostic",
        mode="demo",
    )


class ToxiproxyAdapterContractTests(unittest.TestCase):
    def test_owned_proxy_cleans_only_its_resources_when_body_fails(self) -> None:
        existing = {
            "shared": {
                "name": "shared",
                "listen": "127.0.0.1:18080",
                "upstream": "127.0.0.1:8080",
                "enabled": True,
                "toxics": [],
            }
        }
        state = ContractState(proxies=existing)
        with RunningContractServer(state) as server:
            client = ToxiproxyClient(server.url)
            with self.assertRaisesRegex(PlannedFailure, "planned failure"):
                with OwnedProxy(
                    client,
                    ProxyDefinition("owned", "127.0.0.1:19091", "127.0.0.1:9090"),
                ) as proxy:
                    proxy.add_toxic(
                        ToxicDefinition("owned-latency", "latency", "downstream", {"latency": 1000, "jitter": 0})
                    )
                    raise PlannedFailure("planned failure")

        self.assertEqual(set(state.proxies), {"shared"})
        paths = [record.path for record in state.requests]
        self.assertNotIn("/reset", paths)
        self.assertIn("/proxies/owned/toxics/owned-latency", paths)
        self.assertIn("/proxies/owned", paths)

    def test_collision_is_rejected_without_mutating_existing_proxy(self) -> None:
        existing = {
            "collision": {
                "name": "collision",
                "listen": "127.0.0.1:18080",
                "upstream": "127.0.0.1:8080",
                "enabled": True,
                "toxics": [],
            }
        }
        state = ContractState(proxies=existing)
        with RunningContractServer(state) as server:
            client = ToxiproxyClient(server.url)
            with self.assertRaises(ProxyCollisionError):
                with OwnedProxy(
                    client,
                    ProxyDefinition("collision", "127.0.0.1:19091", "127.0.0.1:9090"),
                ):
                    self.fail("colliding proxy must not be entered")

        self.assertEqual(state.proxies, existing)
        self.assertFalse(any(record.method != "GET" for record in state.requests))

    def test_invalid_create_response_compensates_by_deleting_owned_proxy(self) -> None:
        state = ContractState(invalid_create_response=True)
        with RunningContractServer(state) as server:
            client = ToxiproxyClient(server.url)
            with self.assertRaises(ToxiproxyContractError):
                with OwnedProxy(
                    client,
                    ProxyDefinition("invalid-response", "127.0.0.1:19091", "127.0.0.1:9090"),
                ):
                    self.fail("invalid proxy response must not enter the context")

        self.assertEqual(state.proxies, {})
        self.assertNotIn("/reset", [record.path for record in state.requests])


class FaultSuiteTests(unittest.TestCase):
    def test_all_scenarios_require_actual_outcome_and_recovery(self) -> None:
        observations = [
            healthy(),
            healthy(1.05),
            healthy(),
            FailureObservation(FailureCause.TIMEOUT, 0.5, "read deadline expired"),
            healthy(),
            FailureObservation(FailureCause.RESET, 0.01, "connection reset"),
            healthy(),
            healthy(0.04),
            healthy(),
        ]
        probe = ScriptedProbe(observations)
        with RunningContractServer() as server:
            report = run_fault_suite(ToxiproxyClient(server.url), config("suite-ok"), probe)
            requests = list(server.state.requests)
            proxies = dict(server.state.proxies)

        self.assertEqual([item.name for item in report.scenarios], ["baseline", "latency", "timeout", "reset_peer", "slicer"])
        self.assertEqual(probe.timeouts, [20.0, 20.0, 20.0, 0.5, 20.0, 20.0, 20.0, 20.0, 20.0])
        self.assertEqual(proxies, {})
        self.assertNotIn("/reset", [record.path for record in requests])

    def test_false_latency_outcome_fails_after_recovery_probe(self) -> None:
        probe = ScriptedProbe([healthy(), healthy(0.1), healthy()])
        with RunningContractServer() as server:
            with self.assertRaisesRegex(FaultAssertionError, "latency"):
                run_fault_suite(ToxiproxyClient(server.url), config("bad-latency"), probe)
            proxies = dict(server.state.proxies)

        self.assertEqual(probe.timeouts, [20.0, 20.0, 20.0])
        self.assertEqual(proxies, {})

    def test_missing_recovery_is_a_failure(self) -> None:
        changed = ResultObservation(
            label=ResultLabel("DEMO", "WARNING", "warning"),
            elapsed_seconds=0.02,
            error_kind="none",
            failed_phase=None,
            total_ms=None,
            query_ms=None,
            integrity_verified=True,
        )
        probe = ScriptedProbe([healthy(), healthy(1.05), changed])
        with RunningContractServer() as server:
            with self.assertRaisesRegex(FaultAssertionError, "recovery"):
                run_fault_suite(ToxiproxyClient(server.url), config("bad-recovery"), probe)
            proxies = dict(server.state.proxies)

        self.assertEqual(probe.timeouts, [20.0, 20.0, 20.0])
        self.assertEqual(proxies, {})

    def test_missing_api_reports_skip_and_exit_two(self) -> None:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            _, port = reservation.getsockname()
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            exit_code = main(
                [
                    "--toxiproxy-url",
                    f"http://127.0.0.1:{port}",
                    "--upstream",
                    "127.0.0.1:9090",
                    "--listen",
                    "127.0.0.1:19091",
                ]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("SKIP", stderr.getvalue())


class ProbeIntegrationTests(unittest.TestCase):
    def test_diagnostic_probe_observes_real_framed_demo_result(self) -> None:
        server = DiagnosticServer(("127.0.0.1", 0), DiagnosticHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            observation = DiagnosticProbe(Endpoint(str(host), int(port)), "demo")(20.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

        self.assertIsInstance(observation, ResultObservation)
        assert isinstance(observation, ResultObservation)
        self.assertEqual(observation.label, ResultLabel("DEMO", "NORMAL", "normal"))
        self.assertTrue(observation.integrity_verified)

    def test_database_probe_scopes_proxy_endpoint_to_subprocess_environment(self) -> None:
        result = {
            "mode": "LIVE",
            "status": "NORMAL",
            "scenario": "live",
            "error_kind": "none",
            "failed_phase": None,
            "total_ms": 2.0,
            "query_ms": 1.0,
        }
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(result), stderr="")
        with patch("scripts.fault_probes.subprocess.run", return_value=completed) as run:
            with patch.dict(os.environ, {"DB_HOST": "original", "DB_PORT": "3306"}):
                observation = DatabaseProbe(Endpoint("127.0.0.1", 19091))(20.0)
                self.assertEqual(os.environ["DB_HOST"], "original")
                self.assertEqual(os.environ["DB_PORT"], "3306")

        child_environment = run.call_args.kwargs["env"]
        self.assertEqual(child_environment["DB_HOST"], "127.0.0.1")
        self.assertEqual(child_environment["DB_PORT"], "19091")
        self.assertIsInstance(observation, ResultObservation)


if __name__ == "__main__":
    unittest.main()
