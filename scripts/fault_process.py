from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence


class ProcessDeadlineError(TimeoutError):
    pass


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class ProcessResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def run_bounded_process(
    command: Sequence[str],
    cwd: Path,
    input_bytes: Optional[bytes],
    timeout_seconds: float,
) -> ProcessResult:
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ProcessDeadlineError("Process deadline must be finite and positive.")
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        stdout, stderr = process.communicate(input=input_bytes, timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        process.kill()
        process.communicate()
        raise ProcessDeadlineError("Owned subprocess deadline expired; process was reaped.") from exc
    if time.monotonic() - started > timeout_seconds:
        raise ProcessDeadlineError("Owned subprocess exceeded its deadline after being reaped.")
    return ProcessResult(process.returncode, stdout, stderr)
