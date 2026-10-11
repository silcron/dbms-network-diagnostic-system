from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol, Sequence, Union

from app.client_core import DiagnosticClient
from app.protocol import JsonObject, ProtocolError
from scripts.fault_json import JsonBoundaryError, JsonValue, dumps_finite_json, loads_finite_json
from scripts.fault_process import ProcessDeadlineError, run_bounded_process


WORKER_TIMEOUT = 10
WORKER_RESET = 11
WORKER_PROTOCOL = 12
WORKER_NETWORK = 13


class FaultRunnerError(RuntimeError):
    pass


class FaultAssertionError(FaultRunnerError):
    pass


class PrerequisiteError(FaultRunnerError):
    pass


class EndpointError(FaultRunnerError):
    pass


class FailureCause(Enum):
    TIMEOUT = "timeout"
    RESET = "reset"
    NETWORK = "network"
    PROTOCOL = "protocol"
    PROCESS = "process"


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class Endpoint:
    host: str
    port: int

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @classmethod
    def parse(cls, value: str, require_loopback: bool = False) -> "Endpoint":
        host, separator, raw_port = value.rpartition(":")
        if not separator or not host or not raw_port:
            raise EndpointError("Endpoint must use HOST:PORT format.")
        try:
            port = int(raw_port)
        except ValueError as exc:
            raise EndpointError("Endpoint port must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise EndpointError("Endpoint port must be from 1 to 65535.")
        if require_loopback and host != "127.0.0.1":
            raise EndpointError("Proxy listen endpoint must use 127.0.0.1.")
        return cls(host=host, port=port)


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ResultLabel:
    mode: str
    status: str
    scenario: str


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ResultObservation:
    label: ResultLabel
    elapsed_seconds: float
    error_kind: str
    failed_phase: Optional[str]
    total_ms: Optional[float]
    query_ms: Optional[float]
    integrity_verified: bool


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class FailureObservation:
    cause: FailureCause
    elapsed_seconds: float
    detail: str


Observation = Union[ResultObservation, FailureObservation]


class Probe(Protocol):
    def __call__(self, timeout_seconds: float) -> Observation:
        ...


class DiagnosticProbe:
    def __init__(self, endpoint: Endpoint, mode: str) -> None:
        self.endpoint = endpoint
        self.mode = mode

    def __call__(self, timeout_seconds: float) -> Observation:
        started = time.monotonic()
        try:
            completed = run_bounded_process(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "scripts.fault_probes",
                    "--diagnostic-worker",
                    self.endpoint.host,
                    str(self.endpoint.port),
                    self.mode,
                    str(timeout_seconds),
                ],
                Path(__file__).resolve().parents[1],
                None,
                timeout_seconds,
            )
        except ProcessDeadlineError:
            return FailureObservation(FailureCause.TIMEOUT, time.monotonic() - started, "probe deadline expired")
        except OSError as exc:
            return FailureObservation(FailureCause.PROCESS, time.monotonic() - started, type(exc).__name__)
        elapsed = time.monotonic() - started
        if elapsed > timeout_seconds:
            return FailureObservation(FailureCause.TIMEOUT, elapsed, "probe deadline expired")
        failure_cause = _worker_failure_cause(completed.returncode)
        if failure_cause is not None:
            return FailureObservation(failure_cause, elapsed, f"probe worker exit {completed.returncode}")
        try:
            payload = loads_finite_json(completed.stdout.strip())
        except JsonBoundaryError:
            return FailureObservation(FailureCause.PROCESS, elapsed, "probe worker returned invalid JSON")
        if not isinstance(payload, dict):
            return FailureObservation(FailureCause.PROCESS, elapsed, "probe worker returned a non-object")
        return _diagnostic_observation(payload, elapsed)


class DatabaseProbe:
    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint

    def __call__(self, timeout_seconds: float) -> Observation:
        started = time.monotonic()
        environment = os.environ.copy()
        environment["DB_HOST"] = self.endpoint.host
        environment["DB_PORT"] = str(self.endpoint.port)
        statement = (
            "import json; from app.diagnostics import probe_database; "
            "print(json.dumps(probe_database('normal', mode='live'), ensure_ascii=False))"
        )
        try:
            completed = subprocess.run(
                [sys.executable, "-B", "-c", statement],
                cwd=Path(__file__).resolve().parents[1],
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return FailureObservation(FailureCause.TIMEOUT, time.monotonic() - started, "probe deadline expired")
        except OSError as exc:
            return FailureObservation(FailureCause.PROCESS, time.monotonic() - started, type(exc).__name__)
        elapsed = time.monotonic() - started
        if completed.returncode != 0:
            return FailureObservation(FailureCause.PROCESS, elapsed, f"probe subprocess exit {completed.returncode}")
        try:
            payload = loads_finite_json(completed.stdout.strip())
        except JsonBoundaryError:
            return FailureObservation(FailureCause.PROCESS, elapsed, "probe subprocess returned invalid JSON")
        return _result_observation(payload, elapsed, integrity_verified=True)


def _optional_finite_number(value: JsonValue) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FaultAssertionError("Diagnostic timing field is not numeric.")
    converted = float(value)
    if not math.isfinite(converted) or converted < 0:
        raise FaultAssertionError("Diagnostic timing field is not finite and nonnegative.")
    return converted


def _result_observation(payload: JsonValue, elapsed_seconds: float, integrity_verified: bool) -> Observation:
    if not isinstance(payload, dict):
        return FailureObservation(FailureCause.PROTOCOL, elapsed_seconds, "diagnostic result is not an object")
    mode, status, scenario = payload.get("mode"), payload.get("status"), payload.get("scenario")
    if not isinstance(mode, str) or not isinstance(status, str) or not isinstance(scenario, str):
        return FailureObservation(FailureCause.PROTOCOL, elapsed_seconds, "diagnostic label fields are invalid")
    error_kind = payload.get("error_kind", "none")
    failed_phase = payload.get("failed_phase")
    if not isinstance(error_kind, str) or (failed_phase is not None and not isinstance(failed_phase, str)):
        return FailureObservation(FailureCause.PROTOCOL, elapsed_seconds, "diagnostic cause fields are invalid")
    try:
        total_ms = _optional_finite_number(payload.get("total_ms"))
        query_ms = _optional_finite_number(payload.get("query_ms"))
    except FaultAssertionError as exc:
        return FailureObservation(FailureCause.PROTOCOL, elapsed_seconds, str(exc))
    return ResultObservation(
        label=ResultLabel(mode=mode, status=status, scenario=scenario),
        elapsed_seconds=elapsed_seconds,
        error_kind=error_kind,
        failed_phase=failed_phase,
        total_ms=total_ms,
        query_ms=query_ms,
        integrity_verified=integrity_verified,
    )


def _diagnostic_observation(response: JsonObject, elapsed_seconds: float) -> Observation:
    if response.get("type") != "diagnostic_result":
        return FailureObservation(FailureCause.PROTOCOL, elapsed_seconds, "invalid diagnostic response type")
    trace = response.get("osi_trace")
    integrity = isinstance(trace, list) and len(trace) == 7
    return _result_observation(response.get("result"), elapsed_seconds, integrity)


def _worker_failure_cause(returncode: int) -> Optional[FailureCause]:
    codes = {
        WORKER_TIMEOUT: FailureCause.TIMEOUT,
        WORKER_RESET: FailureCause.RESET,
        WORKER_PROTOCOL: FailureCause.PROTOCOL,
        WORKER_NETWORK: FailureCause.NETWORK,
    }
    if returncode == 0:
        return None
    return codes.get(returncode, FailureCause.PROCESS)


def _diagnostic_worker(values: Sequence[str]) -> int:
    if len(values) != 5:
        return 64
    try:
        port = int(values[2])
        timeout_seconds = float(values[4])
    except ValueError:
        return 64
    client = DiagnosticClient(values[1], port, timeout=timeout_seconds)
    try:
        client.connect()
        response = client.diagnose("normal", mode=values[3])
        try:
            sys.stdout.buffer.write(dumps_finite_json(response).encode("utf-8"))
        except JsonBoundaryError:
            return WORKER_PROTOCOL
        return 0
    except (TimeoutError, socket.timeout):
        return WORKER_TIMEOUT
    except (ConnectionResetError, BrokenPipeError):
        return WORKER_RESET
    except ProtocolError:
        return WORKER_PROTOCOL
    except (ConnectionError, OSError):
        return WORKER_NETWORK
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(_diagnostic_worker(sys.argv[1:]))
