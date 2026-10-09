from __future__ import annotations

import argparse
import math
import os
import sys
import uuid
from dataclasses import dataclass
from typing import Optional, Sequence, Union

from scripts.fault_deadline import Deadline, DeadlineExpiredError
from scripts.fault_probes import (
    DatabaseProbe,
    DiagnosticProbe,
    Endpoint,
    EndpointError,
    FailureCause,
    FailureObservation,
    FaultAssertionError,
    FaultRunnerError,
    Observation,
    PrerequisiteError,
    Probe,
    ResultLabel,
    ResultObservation,
)
from scripts.toxiproxy_adapter import (
    OwnedProxy,
    ProxyCollisionError,
    ProxyDefinition,
    ToxicDefinition,
    ToxiproxyClient,
    ToxiproxyConfigurationError,
    ToxiproxyDeadlineError,
    ToxiproxyError,
    ToxiproxyUnavailableError,
)


NORMAL_PROBE_TIMEOUT_SECONDS = 20.0
BLOCKED_PROBE_TIMEOUT_SECONDS = 0.5
SCENARIO_DEADLINE_SECONDS = 30.0
LATENCY_INCREASE_FLOOR_SECONDS = 0.8


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class RunnerConfig:
    proxy: ProxyDefinition
    listen: Endpoint
    target: str
    mode: str


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ScenarioRecord:
    name: str
    outcome: Observation
    recovery_elapsed_seconds: Optional[float]


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class SuiteReport:
    proxy_name: str
    scenarios: tuple[ScenarioRecord, ...]


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ScenarioDefinition:
    name: str
    toxic: ToxicDefinition
    timeout_seconds: float


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ScenarioContext:
    probe: Probe
    baseline: ResultObservation
    config: RunnerConfig


def _require_result(observation: Observation, context: str) -> ResultObservation:
    if isinstance(observation, FailureObservation):
        raise FaultAssertionError(f"{context} failed with {observation.cause.value}: {observation.detail}")
    if not math.isfinite(observation.elapsed_seconds) or not 0 <= observation.elapsed_seconds <= SCENARIO_DEADLINE_SECONDS:
        raise FaultAssertionError(f"{context} elapsed time exceeded the 30 second bound.")
    if not observation.integrity_verified:
        raise FaultAssertionError(f"{context} did not preserve the complete diagnostic message.")
    return observation


def _assert_baseline(observation: Observation, config: RunnerConfig) -> ResultObservation:
    result = _require_result(observation, "baseline")
    if result.label.mode != config.mode.upper():
        raise FaultAssertionError("baseline mode did not match the requested mode.")
    if config.target == "diagnostic" and config.mode == "demo":
        if result.label.status != "NORMAL" or result.label.scenario != "normal":
            raise FaultAssertionError("diagnostic DEMO baseline was not the labelled normal result.")
    if config.target == "database":
        if result.label.status != "NORMAL" or result.error_kind != "none" or result.failed_phase is not None:
            raise PrerequisiteError("database baseline is not healthy; LIVE fault scenarios are blocked.")
        if result.query_ms is None:
            raise PrerequisiteError("database baseline did not prove a successful SELECT 1 query.")
    return result


def _assert_recovery(observation: Observation, baseline: ResultObservation, scenario: str) -> ResultObservation:
    recovery = _require_result(observation, f"{scenario} recovery")
    if recovery.label != baseline.label:
        raise FaultAssertionError(f"{scenario} recovery did not restore the baseline result.")
    return recovery


def _assert_fault_outcome(definition: ScenarioDefinition, observation: Observation, context: ScenarioContext) -> None:
    if not math.isfinite(observation.elapsed_seconds) or not 0 <= observation.elapsed_seconds <= SCENARIO_DEADLINE_SECONDS:
        raise FaultAssertionError(f"{definition.name} outcome exceeded the 30 second bound.")
    if definition.name == "latency":
        result = _require_result(observation, "latency")
        if context.config.target == "database":
            if (
                result.label.status != "WARNING"
                or result.error_kind != "none"
                or result.failed_phase is not None
                or result.total_ms is None
                or result.total_ms <= 500
                or result.query_ms is None
            ):
                raise FaultAssertionError("latency did not preserve SELECT 1 and produce a slow WARNING result.")
        elif (
            result.label != context.baseline.label
            or result.elapsed_seconds < context.baseline.elapsed_seconds + LATENCY_INCREASE_FLOOR_SECONDS
        ):
            raise FaultAssertionError("latency did not retain the labelled result with the expected delay.")
        return
    if definition.name == "timeout":
        if isinstance(observation, FailureObservation) and observation.cause == FailureCause.TIMEOUT:
            return
        if isinstance(observation, ResultObservation) and observation.error_kind == "timeout":
            return
        raise FaultAssertionError("timeout toxic did not produce an observed timeout outcome.")
    if definition.name == "reset_peer":
        if isinstance(observation, FailureObservation) and observation.cause in {
            FailureCause.RESET,
            FailureCause.NETWORK,
            FailureCause.PROTOCOL,
        }:
            return
        if isinstance(observation, ResultObservation) and observation.error_kind == "network":
            return
        raise FaultAssertionError("reset_peer toxic did not produce an observed connection failure.")
    result = _require_result(observation, "slicer")
    if result.label != context.baseline.label:
        raise FaultAssertionError("slicer did not preserve the complete framed result.")


def _scenario_definitions(proxy_name: str) -> tuple[ScenarioDefinition, ...]:
    return (
        ScenarioDefinition(
            "latency",
            ToxicDefinition(f"{proxy_name}-latency", "latency", "downstream", {"latency": 1000, "jitter": 0}),
            NORMAL_PROBE_TIMEOUT_SECONDS,
        ),
        ScenarioDefinition(
            "timeout",
            ToxicDefinition(f"{proxy_name}-timeout", "timeout", "downstream", {"timeout": 0}),
            BLOCKED_PROBE_TIMEOUT_SECONDS,
        ),
        ScenarioDefinition(
            "reset_peer",
            ToxicDefinition(f"{proxy_name}-reset", "reset_peer", "downstream", {"timeout": 0}),
            NORMAL_PROBE_TIMEOUT_SECONDS,
        ),
        ScenarioDefinition(
            "slicer",
            ToxicDefinition(
                f"{proxy_name}-slicer",
                "slicer",
                "downstream",
                {"average_size": 16, "size_variation": 8, "delay": 100},
            ),
            NORMAL_PROBE_TIMEOUT_SECONDS,
        ),
    )


def _run_scenario(owned_proxy: OwnedProxy, definition: ScenarioDefinition, context: ScenarioContext) -> ScenarioRecord:
    deadline = Deadline.after(SCENARIO_DEADLINE_SECONDS)
    try:
        owned_proxy.add_toxic(definition.toxic, deadline)
        try:
            outcome_timeout = deadline.remaining(
                definition.timeout_seconds,
                owned_proxy.client.timeout_seconds,
            )
            outcome = context.probe(outcome_timeout)
        finally:
            owned_proxy.remove_toxic(definition.toxic.name, deadline)
        recovery_timeout = deadline.remaining(NORMAL_PROBE_TIMEOUT_SECONDS)
        recovery = _assert_recovery(context.probe(recovery_timeout), context.baseline, definition.name)
        deadline.remaining()
    except (DeadlineExpiredError, ToxiproxyDeadlineError) as exc:
        raise FaultAssertionError(f"{definition.name} scenario deadline exceeded the 30 second bound.") from exc
    _assert_fault_outcome(definition, outcome, context)
    return ScenarioRecord(definition.name, outcome, recovery.elapsed_seconds)


def run_fault_suite(client: ToxiproxyClient, config: RunnerConfig, probe: Probe) -> SuiteReport:
    original_proxies = client.list_proxies()
    pending_error: Optional[Union[FaultRunnerError, ToxiproxyError]] = None
    records: list[ScenarioRecord] = []
    try:
        with OwnedProxy(client, config.proxy) as owned_proxy:
            baseline = _assert_baseline(probe(NORMAL_PROBE_TIMEOUT_SECONDS), config)
            records.append(ScenarioRecord("baseline", baseline, None))
            context = ScenarioContext(probe=probe, baseline=baseline, config=config)
            for definition in _scenario_definitions(config.proxy.name):
                records.append(_run_scenario(owned_proxy, definition, context))
    except ToxiproxyUnavailableError:
        pending_error = FaultAssertionError("Toxiproxy API became unavailable after successful preflight.")
    except (FaultRunnerError, ToxiproxyError) as exc:
        pending_error = exc
    try:
        final_proxies = client.list_proxies()
    except ToxiproxyUnavailableError as exc:
        raise FaultAssertionError(
            "Toxiproxy API became unavailable after preflight; cleanup could not be verified."
        ) from exc
    if final_proxies != original_proxies:
        raise FaultAssertionError("Toxiproxy cleanup changed pre-existing proxies or left the owned proxy behind.")
    if pending_error is not None:
        raise pending_error
    return SuiteReport(proxy_name=config.proxy.name, scenarios=tuple(records))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run isolated optional Toxiproxy fault scenarios.")
    parser.add_argument("--upstream", required=True, help="Existing upstream endpoint as HOST:PORT.")
    parser.add_argument("--listen", required=True, help="Unused owned proxy endpoint as 127.0.0.1:PORT.")
    parser.add_argument("--toxiproxy-url", default="http://127.0.0.1:8474")
    parser.add_argument("--target", choices=("diagnostic", "database"), default="diagnostic")
    parser.add_argument("--mode", choices=("demo", "live"), default="demo")
    return parser


def _runner_config(args: argparse.Namespace) -> RunnerConfig:
    upstream = Endpoint.parse(args.upstream)
    listen = Endpoint.parse(args.listen, require_loopback=True)
    if args.target == "database" and args.mode != "live":
        raise PrerequisiteError("database target requires --mode live.")
    proxy_name = f"dbms-fault-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    return RunnerConfig(
        proxy=ProxyDefinition(proxy_name, listen.address, upstream.address),
        listen=listen,
        target=args.target,
        mode=args.mode,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = _runner_config(args)
        client = ToxiproxyClient(args.toxiproxy_url)
        probe: Probe
        if config.target == "diagnostic":
            probe = DiagnosticProbe(config.listen, config.mode)
        else:
            probe = DatabaseProbe(config.listen)
        report = run_fault_suite(client, config, probe)
    except (PrerequisiteError, EndpointError, ToxiproxyConfigurationError, ToxiproxyUnavailableError) as exc:
        print(f"SKIP: {exc}", file=sys.stderr)
        return 2
    except (FaultAssertionError, ProxyCollisionError, ToxiproxyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    for scenario in report.scenarios:
        recovery = "" if scenario.recovery_elapsed_seconds is None else f", recovery={scenario.recovery_elapsed_seconds:.3f}s"
        print(f"PASS {scenario.name}: outcome={scenario.outcome.elapsed_seconds:.3f}s{recovery}")
    print(f"PASS cleanup: removed owned proxy {report.proxy_name}; pre-existing proxies unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
