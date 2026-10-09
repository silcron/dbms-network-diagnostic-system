from __future__ import annotations

import json
from math import isfinite
from typing import Final

from .metrics import DiagnosticObservation, DiagnosticStatus, ErrorKind, ResultMode
from .protocol import JsonObject, JsonValue, ProtocolError

_RESULT_MODES: Final[dict[str, ResultMode]] = {"LIVE": "live", "DEMO": "demo"}
_STATUSES: Final[dict[str, DiagnosticStatus]] = {"NORMAL": "NORMAL", "WARNING": "WARNING", "DOWN": "DOWN"}
_ERROR_KINDS: Final[dict[str, ErrorKind]] = {
    "none": "none",
    "config": "config",
    "dependency": "dependency",
    "network": "network",
    "timeout": "timeout",
    "auth": "auth",
    "database": "database",
    "query": "query",
    "internal": "internal",
}


def _require_string(container: JsonObject, field: str) -> str:
    value = container.get(field)
    if not isinstance(value, str):
        raise ProtocolError(f"Diagnostic response contains an invalid {field} field.")
    return value


def _require_nonnegative_number(container: JsonObject, field: str) -> float:
    value = container.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"Diagnostic response contains an invalid {field} field.")
    try:
        parsed = float(value)
    except OverflowError as exc:
        raise ProtocolError(f"Diagnostic response contains an invalid {field} field.") from exc
    if parsed < 0 or not isfinite(parsed):
        raise ProtocolError(f"Diagnostic response contains an invalid {field} field.")
    return parsed


def _validate_trace_entry(value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ProtocolError("Diagnostic response contains an invalid OSI trace entry.")
    for field in ("layer", "application"):
        if not _require_string(value, field).strip():
            raise ProtocolError(f"Diagnostic response contains an invalid OSI {field} field.")


def parse_diagnostic_observation(response: JsonObject) -> DiagnosticObservation:
    if response.get("type") != "diagnostic_result":
        raise ProtocolError("Diagnostic server returned an invalid response type.")
    _require_string(response, "checked_at")
    _require_nonnegative_number(response, "client_rtt_ms")

    result = response.get("result")
    if not isinstance(result, dict):
        raise ProtocolError("Diagnostic response has no result object.")
    for field in ("scenario", "database", "detail"):
        _require_string(result, field)

    raw_mode = _require_string(result, "mode")
    mode = _RESULT_MODES.get(raw_mode)
    raw_status = _require_string(result, "status")
    status = _STATUSES.get(raw_status)
    raw_error_kind = result.get("error_kind", "none")
    error_kind = _ERROR_KINDS.get(raw_error_kind) if isinstance(raw_error_kind, str) else None
    if mode is None or status is None or error_kind is None:
        raise ProtocolError("Diagnostic response contains an invalid metric label.")

    trace = response.get("osi_trace")
    if not isinstance(trace, list) or len(trace) != 7:
        raise ProtocolError("Diagnostic response must contain exactly seven OSI trace entries.")
    for entry in trace:
        _validate_trace_entry(entry)

    raw_total_ms = result.get("total_ms")
    total_ms = None if raw_total_ms is None else _require_nonnegative_number(result, "total_ms")
    return DiagnosticObservation(mode, status, error_kind, total_ms)


def encode_json_object(payload: JsonObject) -> bytes:
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProtocolError("Diagnostic server returned an invalid JSON value.") from exc
