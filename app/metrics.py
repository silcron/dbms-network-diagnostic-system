from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from typing import Final, Literal

RequestMode = Literal["live", "demo", "unknown"]
ResultMode = Literal["live", "demo"]
RequestOutcome = Literal[
    "success",
    "invalid_request",
    "upstream_failure",
    "upstream_timeout",
    "internal_error",
]
DiagnosticStatus = Literal["NORMAL", "WARNING", "DOWN"]
ErrorKind = Literal[
    "none",
    "config",
    "dependency",
    "network",
    "timeout",
    "auth",
    "database",
    "query",
    "internal",
]

_REQUEST_MODES: Final = frozenset({"live", "demo", "unknown"})
_RESULT_MODES: Final = frozenset({"live", "demo"})
_REQUEST_OUTCOMES: Final = frozenset(
    {"success", "invalid_request", "upstream_failure", "upstream_timeout", "internal_error"}
)
_STATUSES: Final = frozenset({"NORMAL", "WARNING", "DOWN"})
_ERROR_KINDS: Final = frozenset(
    {"none", "config", "dependency", "network", "timeout", "auth", "database", "query", "internal"}
)


class InvalidMetricValue(ValueError):
    field: str
    value: str

    def __init__(self, field: str, value: str) -> None:
        self.field = field
        self.value = value
        super().__init__(f"invalid {field}: {value}")


def _require_member(field: str, value: str, allowed: frozenset[str]) -> None:
    if value not in allowed:
        raise InvalidMetricValue(field, repr(value))


@dataclass(frozen=True)  # noqa: SLOTS_OK
class DiagnosticObservation:
    mode: ResultMode
    status: DiagnosticStatus
    error_kind: ErrorKind
    total_ms: float | None

    def __post_init__(self) -> None:
        _require_member("mode", self.mode, _RESULT_MODES)
        _require_member("status", self.status, _STATUSES)
        _require_member("error_kind", self.error_kind, _ERROR_KINDS)
        if self.total_ms is None:
            return
        if isinstance(self.total_ms, bool) or not isinstance(self.total_ms, (int, float)):
            raise InvalidMetricValue("total_ms", repr(self.total_ms))
        if self.total_ms < 0 or not isfinite(self.total_ms):
            raise InvalidMetricValue("total_ms", repr(self.total_ms))


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _format_number(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and isfinite(value):
        return format(value, ".17g")
    raise InvalidMetricValue("sample", repr(value))


class WebMetrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests: Counter[tuple[str, str]] = Counter()
        self._results: Counter[tuple[str, str, str]] = Counter()
        self._duration_sum: dict[str, float] = {}
        self._duration_count: Counter[str] = Counter()
        self._last_success: dict[str, int] = {}

    def observe_request(self, mode: RequestMode, outcome: RequestOutcome) -> None:
        _require_member("mode", mode, _REQUEST_MODES)
        _require_member("outcome", outcome, _REQUEST_OUTCOMES)
        with self._lock:
            self._requests[(mode, outcome)] += 1

    def observe_result(self, observation: DiagnosticObservation) -> None:
        duration_seconds = None if observation.total_ms is None else observation.total_ms / 1000.0
        with self._lock:
            if duration_seconds is not None:
                next_sum = self._duration_sum.get(observation.mode, 0.0) + duration_seconds
                if not isfinite(next_sum):
                    raise InvalidMetricValue("duration_seconds_sum", repr(next_sum))
            self._results[(observation.mode, observation.status, observation.error_kind)] += 1
            self._last_success[observation.mode] = int(observation.status == "NORMAL")
            if duration_seconds is not None:
                self._duration_sum[observation.mode] = next_sum
                self._duration_count[observation.mode] += 1

    def render(self) -> str:
        with self._lock:
            requests = dict(self._requests)
            results = dict(self._results)
            duration_sum = dict(self._duration_sum)
            duration_count = dict(self._duration_count)
            last_success = dict(self._last_success)

        lines = [
            "# HELP dbms_diagnostic_requests_total Requests observed by this web process; resets on restart.",
            "# TYPE dbms_diagnostic_requests_total counter",
        ]
        for (mode, outcome), value in sorted(requests.items()):
            lines.append(
                "dbms_diagnostic_requests_total"
                f'{{mode="{_escape_label(mode)}",outcome="{_escape_label(outcome)}"}} {_format_number(value)}'
            )
        lines.extend(
            (
                "# HELP dbms_diagnostic_results_total Diagnostic results observed through this web process.",
                "# TYPE dbms_diagnostic_results_total counter",
            )
        )
        for (mode, status, error_kind), value in sorted(results.items()):
            lines.append(
                "dbms_diagnostic_results_total"
                f'{{mode="{_escape_label(mode)}",status="{_escape_label(status)}",'
                f'error_kind="{_escape_label(error_kind)}"}} {_format_number(value)}'
            )
        lines.extend(
            (
                "# HELP dbms_diagnostic_duration_seconds Valid diagnostic total durations observed by this web process.",
                "# TYPE dbms_diagnostic_duration_seconds summary",
            )
        )
        for mode, value in sorted(duration_sum.items()):
            lines.append(
                f'dbms_diagnostic_duration_seconds_sum{{mode="{_escape_label(mode)}"}} {_format_number(value)}'
            )
            lines.append(
                "dbms_diagnostic_duration_seconds_count"
                f'{{mode="{_escape_label(mode)}"}} {_format_number(duration_count[mode])}'
            )
        lines.extend(
            (
                "# HELP dbms_diagnostic_last_success Last observed result was NORMAL; absent means unobserved.",
                "# TYPE dbms_diagnostic_last_success gauge",
            )
        )
        for mode, value in sorted(last_success.items()):
            lines.append(f'dbms_diagnostic_last_success{{mode="{_escape_label(mode)}"}} {_format_number(value)}')
        return "\n".join(lines) + "\n"
