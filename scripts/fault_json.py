from __future__ import annotations

import json
import math
from typing import Union


JsonPrimitive = Union[None, bool, int, float, str]
JsonValue = Union[JsonPrimitive, list["JsonValue"], dict[str, "JsonValue"]]
JsonObject = dict[str, JsonValue]


class JsonBoundaryError(ValueError):
    pass


def _reject_nonfinite(token: str) -> JsonValue:
    raise JsonBoundaryError(f"JSON number {token!r} is not finite.")


def _finite_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise JsonBoundaryError(f"JSON number {token!r} is not finite.")
    return value


def loads_finite_json(data: Union[str, bytes]) -> JsonValue:
    try:
        value: JsonValue = json.loads(
            data,
            parse_constant=_reject_nonfinite,
            parse_float=_finite_float,
        )
    except JsonBoundaryError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise JsonBoundaryError("Input is not finite UTF-8 JSON.") from exc
    return value


def dumps_finite_json(value: JsonValue) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise JsonBoundaryError("Value cannot be encoded as finite JSON.") from exc
