"""Run one diagnosis across the MariaDB endpoints configured on this server."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from typing import Final

from .db_probe import probe_live_database
from .diagnostics import probe_database
from .protocol import MAX_DB_TARGETS, JsonObject

_ENDPOINT_FIELDS: Final = ("HOST", "PORT", "USER", "PASSWORD", "NAME")


def _prefix(slot: int) -> str:
    return "DB_" if slot == 1 else f"DB{slot}_"


def _probe_target(slot: int) -> JsonObject:
    return {"target_id": slot, **probe_live_database(_prefix(slot))}


def diagnose_targets(scenario: str, mode: str) -> list[JsonObject]:
    slots = [
        slot for slot in range(1, MAX_DB_TARGETS + 1)
        if any(_prefix(slot) + field in os.environ for field in _ENDPOINT_FIELDS)
    ]
    if mode == "demo" or (mode == "auto" and not slots):
        scenarios = ("normal", "warning", "down") if scenario == "mixed" else (scenario,)
        return [
            {"target_id": slot, **probe_database(value, "demo")}
            for slot, value in enumerate(scenarios, 1)
        ]
    if len(slots) < 2:
        return [_probe_target(slots[0] if slots else 1)]
    with ThreadPoolExecutor(max_workers=len(slots)) as pool:
        return list(pool.map(_probe_target, slots))
