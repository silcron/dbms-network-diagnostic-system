from __future__ import annotations

from typing import Literal, NamedTuple, Optional, TypedDict


Status = Literal["NORMAL", "WARNING", "DOWN"]
FailedPhase = Optional[Literal["config", "dependency", "connect", "query"]]
ErrorKind = Literal["none", "config", "dependency", "network", "timeout", "auth", "database", "query", "internal"]


class LiveDiagnosticCore(TypedDict):
    mode: Literal["LIVE"]
    scenario: Literal["live"]
    status: Status
    database: str
    detail: str
    database_rtt_ms: float
    failed_phase: FailedPhase
    error_kind: ErrorKind
    db_errno: Optional[int]
    query_ms: Optional[float]
    total_ms: float


class LiveDiagnosticResult(LiveDiagnosticCore, total=False):
    database_connect_ms: float


class DemoDiagnosticResult(TypedDict):
    mode: Literal["DEMO"]
    scenario: str
    status: str
    database: str
    detail: str


class DbConfig(NamedTuple):
    host: str
    port: int
    user: str
    password: str
    database_name: Optional[str]
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    warning_ms: float


class ProbeFacts(NamedTuple):
    successful: bool
    failed_phase: FailedPhase
    error_kind: ErrorKind
    db_errno: Optional[int]
    connect_ms: Optional[float]
    query_ms: Optional[float]


class ProbeOutcome(NamedTuple):
    status: Status
    detail: str
    failed_phase: FailedPhase
    error_kind: ErrorKind
    db_errno: Optional[int]
    connect_ms: Optional[float]
    query_ms: Optional[float]
