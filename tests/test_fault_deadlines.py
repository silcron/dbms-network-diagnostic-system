from __future__ import annotations

import io
import subprocess
import threading
import time
import unittest
from contextlib import redirect_stderr
from typing import Union
from unittest.mock import patch

from app.protocol import JsonObject
from app.server import DiagnosticHandler, DiagnosticServer
from fault_contract_server import ContractState, RunningContractServer
from scripts.check_fault_scenarios import (
    RunnerConfig,
    ScenarioContext,
    _run_scenario,
    _scenario_definitions,
    main,
)
from scripts.fault_probes import (
    DatabaseProbe,
    DiagnosticProbe,
    Endpoint,
    FailureCause,
    FailureObservation,
    FaultAssertionError,
    ResultLabel,
    ResultObservation,
)
from scripts.fault_json import JsonValue
from scripts.toxiproxy_adapter import (
    OwnedProxy,
    ProxyDefinition,
    ToxicDefinition,
    ToxiproxyClient,
    ToxiproxyContractError,
    ToxiproxyUnavailableError,
)


def _healthy(elapsed: float = 0.02) -> ResultObservation:
    return ResultObservation(
        label=ResultLabel(mode="DEMO", status="NORMAL", scenario="normal"),
        elapsed_seconds=elapsed,
        error_kind="none",
        failed_phase=None,
        total_ms=None,
        query_ms=None,
        integrity_verified=True,
    )


def _config(name: str) -> RunnerConfig:
    return RunnerConfig(
        proxy=ProxyDefinition(name=name, listen="127.0.0.1:19091", upstream="127.0.0.1:9090"),
        listen=Endpoint(host="127.0.0.1", port=19091),
        target="diagnostic",
        mode="demo",
    )


class SlowBudgetProbe:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.calls = 0

    def __call__(self, timeout_seconds: float) -> Union[ResultObservation, FailureObservation]:
        self.calls += 1
        if self.calls == 1:
            return _healthy()
        time.sleep(min(self.delay_seconds, timeout_seconds))
        return _healthy(1.05)


class DelayedDiagnosticHandler(DiagnosticHandler):
    delay_seconds = 0.18
    finished = threading.Event()

    def handle(self) -> None:
        try:
            super().handle()
        finally:
            self.finished.set()

    def dispatch(self, request: JsonObject, peer_ip: str) -> JsonObject:
        time.sleep(self.delay_seconds)
        return super().dispatch(request, peer_ip)


class AbsoluteDeadlineTests(unittest.TestCase):
    def test_api_timeout_bounds_a_trickling_response_as_one_operation(self) -> None:
        state = ContractState(raw_response=b'{"unexpected":{}}', response_byte_delay_seconds=0.04)
        with RunningContractServer(state) as server:
            started = time.monotonic()
            with self.assertRaises(ToxiproxyUnavailableError):
                ToxiproxyClient(server.url, timeout_seconds=0.1).list_proxies()
            elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.35)

    def test_scenario_deadline_bounds_outcome_recovery_and_cleanup(self) -> None:
        probe = SlowBudgetProbe(delay_seconds=2.0)
        with RunningContractServer() as server:
            suite_config = _config("bounded")
            client = ToxiproxyClient(server.url, timeout_seconds=0.5)
            with OwnedProxy(client, suite_config.proxy) as owned_proxy:
                baseline = probe(20.0)
                assert isinstance(baseline, ResultObservation)
                context = ScenarioContext(probe, baseline, suite_config)
                started = time.monotonic()
                with patch("scripts.check_fault_scenarios.SCENARIO_DEADLINE_SECONDS", 2.0):
                    with self.assertRaisesRegex(FaultAssertionError, "deadline|bound"):
                        _run_scenario(owned_proxy, _scenario_definitions("bounded")[0], context)
                elapsed = time.monotonic() - started
            proxies = dict(server.state.proxies)

        self.assertLess(elapsed, 2.2)
        self.assertEqual(proxies, {})

    def test_diagnostic_probe_timeout_is_whole_probe_deadline(self) -> None:
        DelayedDiagnosticHandler.finished.clear()
        server = DiagnosticServer(("127.0.0.1", 0), DelayedDiagnosticHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        try:
            observation = DiagnosticProbe(Endpoint(str(host), int(port)), "demo")(0.22)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            handler_finished = DelayedDiagnosticHandler.finished.wait(timeout=1)

        self.assertIsInstance(observation, FailureObservation)
        assert isinstance(observation, FailureObservation)
        self.assertEqual(observation.cause, FailureCause.TIMEOUT)
        self.assertLess(observation.elapsed_seconds, 0.5)
        self.assertTrue(handler_finished)

    def test_runtime_api_loss_after_proxy_creation_is_failure_not_skip(self) -> None:
        state = ContractState(drop_responses_after_requests=4)
        probe = SlowBudgetProbe(delay_seconds=0.0)
        stderr = io.StringIO()
        with RunningContractServer(state) as server:
            with patch("scripts.check_fault_scenarios.DiagnosticProbe", return_value=probe):
                with redirect_stderr(stderr):
                    exit_code = main(
                        [
                            "--toxiproxy-url",
                            server.url,
                            "--upstream",
                            "127.0.0.1:9090",
                            "--listen",
                            "127.0.0.1:19091",
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertIn("FAIL", stderr.getvalue())
        self.assertIn("cleanup could not be verified", stderr.getvalue())


class JsonBoundaryTests(unittest.TestCase):
    def test_toxiproxy_json_runtime_limits_are_contract_errors(self) -> None:
        payloads = (
            b"[" * 5000 + b"0" + b"]" * 5000,
            b'{"number":' + b"9" * 4301 + b"}",
        )
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                with RunningContractServer(ContractState(raw_response=payload)) as server:
                    with self.assertRaises(ToxiproxyContractError):
                        ToxiproxyClient(server.url).list_proxies()

    def test_proxy_canonicalization_normalizes_recursion(self) -> None:
        payload: JsonValue = {"name": "safe"}
        with patch("scripts.fault_json.json.dumps", side_effect=RecursionError("runtime limit")):
            with self.assertRaisesRegex(ToxiproxyContractError, "canonicalized"):
                ToxiproxyClient._canonical_payload(payload)

    def test_nonfinite_toxic_is_rejected_and_owned_proxy_is_cleaned(self) -> None:
        with RunningContractServer() as server:
            client = ToxiproxyClient(server.url)
            with OwnedProxy(client, _config("finite-json").proxy) as proxy:
                with self.assertRaises(ToxiproxyContractError):
                    proxy.add_toxic(
                        ToxicDefinition("bad", "latency", "downstream", {"latency": float("nan")})
                    )
            proxies = dict(server.state.proxies)

        self.assertEqual(proxies, {})

    def test_database_probe_normalizes_json_runtime_limits(self) -> None:
        payloads = (
            "[" * 500 + "0" + "]" * 500,
            '{"number":' + "9" * 4301 + "}",
        )
        expected_causes = (FailureCause.PROTOCOL, FailureCause.PROCESS)
        for index, (payload, expected_cause) in enumerate(zip(payloads, expected_causes)):
            with self.subTest(index=index):
                completed = subprocess.CompletedProcess([], 0, stdout=payload, stderr="")
                with patch("scripts.fault_probes.subprocess.run", return_value=completed):
                    observation = DatabaseProbe(Endpoint("127.0.0.1", 19091))(1.0)

                self.assertIsInstance(observation, FailureObservation)
                assert isinstance(observation, FailureObservation)
                self.assertEqual(observation.cause, expected_cause)


if __name__ == "__main__":
    unittest.main()
