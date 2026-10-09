from __future__ import annotations

import json
from dataclasses import dataclass
from math import isfinite
from typing import Dict, Final, List, Literal, Union

from .metrics import RequestMode, ResultMode

JsonValue = Union[str, int, float, bool, None, List["JsonValue"], Dict[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]
InputErrorCode = Literal["invalid_request", "invalid_mode", "invalid_scenario", "unsupported_type"]
DemoScenario = Literal["normal", "warning", "down"]

_MODES: Final[Dict[str, ResultMode]] = {"live": "live", "demo": "demo"}
_SCENARIOS: Final[Dict[str, DemoScenario]] = {"normal": "normal", "warning": "warning", "down": "down"}


class HttpInputError(ValueError):
    code: InputErrorCode
    message: str
    mode: RequestMode

    def __init__(self, code: InputErrorCode, message: str, mode: RequestMode = "unknown") -> None:
        self.code = code
        self.message = message
        self.mode = mode
        super().__init__(message)


@dataclass(frozen=True)  # noqa: SLOTS_OK
class DiagnosticRequest:
    host: str
    port: int
    mode: ResultMode
    demo_scenario: DemoScenario


def _parse_float(raw: str) -> float:
    value = float(raw)
    if not isfinite(value):
        raise HttpInputError("invalid_request", "request body must contain finite JSON values")
    return value


def _reject_constant(raw: str) -> float:
    raise HttpInputError("invalid_request", "request body must contain finite JSON values")


def parse_diagnostic_request(data: bytes) -> DiagnosticRequest:
    try:
        payload: JsonValue = json.loads(
            data.decode("utf-8"),
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
    except HttpInputError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise HttpInputError("invalid_request", "request body must be valid UTF-8 JSON") from exc

    if not isinstance(payload, dict):
        raise HttpInputError("invalid_request", "request body must be a JSON object")

    raw_mode = payload.get("mode", "live")
    metric_mode: RequestMode = _MODES.get(raw_mode, "unknown") if isinstance(raw_mode, str) else "unknown"

    raw_host = payload.get("host", "127.0.0.1")
    if not isinstance(raw_host, str):
        raise HttpInputError("unsupported_type", "host must be a string", metric_mode)
    host = raw_host.strip()
    if not host:
        raise HttpInputError("invalid_request", "host must not be empty", metric_mode)

    raw_port = payload.get("port", 9090)
    if isinstance(raw_port, bool) or not isinstance(raw_port, int):
        raise HttpInputError("unsupported_type", "port must be an integer", metric_mode)
    if not 1 <= raw_port <= 65535:
        raise HttpInputError("invalid_request", "port must be between 1 and 65535", metric_mode)

    if not isinstance(raw_mode, str):
        raise HttpInputError("unsupported_type", "mode must be a string")
    mode = _MODES.get(raw_mode)
    if mode is None:
        raise HttpInputError("invalid_mode", "mode must be live or demo")

    raw_scenario = payload.get("demo_scenario", "normal")
    if not isinstance(raw_scenario, str):
        raise HttpInputError("unsupported_type", "demo_scenario must be a string", metric_mode)
    scenario = _SCENARIOS.get(raw_scenario)
    if scenario is None:
        raise HttpInputError(
            "invalid_scenario",
            "demo_scenario must be normal, warning, or down",
            metric_mode,
        )

    return DiagnosticRequest(host, raw_port, mode, scenario)
