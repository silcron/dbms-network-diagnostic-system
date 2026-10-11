from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import BinaryIO, Final, NamedTuple, Sequence


class CommandLimits(NamedTuple):
    timeout_seconds: float
    stdout_bytes: int
    stderr_bytes: int


class CommandResult(NamedTuple):
    stdout: bytes
    stderr: bytes
    returncode: int


class BoundedCommandError(RuntimeError):
    def __init__(self, kind: str, detail: str) -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(detail)


DEFAULT_COMMAND_LIMITS: Final = CommandLimits(
    timeout_seconds=15.0,
    stdout_bytes=4 * 1024 * 1024,
    stderr_bytes=4 * 1024 * 1024,
)


class _OutputCollector:
    __slots__ = ("chunks", "overflow", "_limit", "_process")

    def __init__(self, limit: int, process: subprocess.Popen[bytes]) -> None:
        self.chunks: list[bytes] = []
        self.overflow = False
        self._limit = limit
        self._process = process

    def read(self, pipe: BinaryIO) -> None:
        collected = 0
        while True:
            chunk = pipe.read(64 * 1024)
            if not chunk:
                return
            collected += len(chunk)
            if collected > self._limit:
                self.overflow = True
                try:
                    self._process.kill()
                except OSError:
                    return
                return
            self.chunks.append(chunk)

    def value(self) -> bytes:
        return b"".join(self.chunks)


def _safe_stderr(stderr: bytes) -> str:
    text = stderr.decode("utf-8", errors="replace")
    compact = " ".join(text.split())
    return compact[:512] if compact else "no stderr output"


def run_bounded(
    command: Sequence[str], limits: CommandLimits = DEFAULT_COMMAND_LIMITS
) -> CommandResult:
    if not command:
        raise BoundedCommandError("invalid_command", "command is empty")
    if limits.timeout_seconds <= 0 or limits.stdout_bytes <= 0 or limits.stderr_bytes <= 0:
        raise BoundedCommandError("invalid_limits", "command limits must be positive")

    executable = Path(command[0]).name or command[0]
    try:
        with subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process:
            if process.stdout is None or process.stderr is None:
                process.kill()
                raise BoundedCommandError(
                    "pipe_setup", f"unable to capture output from {executable}"
                )
            stdout = _OutputCollector(limits.stdout_bytes, process)
            stderr = _OutputCollector(limits.stderr_bytes, process)
            stdout_thread = threading.Thread(
                target=stdout.read, args=(process.stdout,), daemon=True
            )
            stderr_thread = threading.Thread(
                target=stderr.read, args=(process.stderr,), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()
            timed_out = False
            try:
                process.wait(timeout=limits.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                process.wait(timeout=1.0)
            stdout_thread.join(timeout=1.0)
            stderr_thread.join(timeout=1.0)

            if timed_out:
                raise BoundedCommandError(
                    "timeout",
                    f"{executable} exceeded {limits.timeout_seconds:g} seconds",
                )
            if stdout.overflow:
                raise BoundedCommandError(
                    "stdout_limit",
                    f"{executable} stdout exceeded {limits.stdout_bytes} bytes",
                )
            if stderr.overflow:
                raise BoundedCommandError(
                    "stderr_limit",
                    f"{executable} stderr exceeded {limits.stderr_bytes} bytes",
                )
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                process.kill()
                raise BoundedCommandError(
                    "pipe_timeout", f"{executable} output pipes did not close"
                )
            result = CommandResult(stdout.value(), stderr.value(), process.returncode)
    except OSError as exc:
        detail = exc.strerror or type(exc).__name__
        raise BoundedCommandError(
            "start_failed", f"unable to start {executable}: {detail}"
        ) from exc

    if result.returncode != 0:
        raise BoundedCommandError(
            "command_failed",
            f"{executable} exited {result.returncode}: {_safe_stderr(result.stderr)}",
        )
    return result
