from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.fault_process import ProcessDeadlineError, run_bounded_process


MAX_HTTP_RESPONSE_BYTES = 1_048_576
WORKER_NETWORK_ERROR = 2


class HttpTransportError(RuntimeError):
    pass


class HttpTransportTimeout(HttpTransportError):
    pass


class HttpTransportUnavailable(HttpTransportError):
    pass


@dataclass(frozen=True)  # noqa: SLOTS_OK - Python 3.9 compatibility
class HttpResponse:
    status: int
    body: bytes


def perform_http_request(method: str, url: str, body: Optional[bytes], timeout_seconds: float) -> HttpResponse:
    command = [
        sys.executable,
        "-B",
        "-m",
        "scripts.fault_transport",
        "--worker",
        method,
        url,
        str(timeout_seconds),
    ]
    try:
        completed = run_bounded_process(
            command,
            Path(__file__).resolve().parents[1],
            body,
            timeout_seconds,
        )
    except ProcessDeadlineError as exc:
        raise HttpTransportTimeout("HTTP operation deadline expired.") from exc
    if completed.returncode == WORKER_NETWORK_ERROR:
        detail = completed.stderr.decode("ascii", errors="replace").strip() or "network error"
        raise HttpTransportUnavailable(detail)
    if completed.returncode != 0:
        raise HttpTransportError(f"HTTP worker exited with status {completed.returncode}.")
    status_line, separator, response_body = completed.stdout.partition(b"\n")
    if not separator:
        raise HttpTransportError("HTTP worker returned an invalid response envelope.")
    try:
        status = int(status_line.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise HttpTransportError("HTTP worker returned an invalid status.") from exc
    return HttpResponse(status=status, body=response_body)


def _worker_request(method: str, url: str, timeout_seconds: float) -> int:
    body = sys.stdin.buffer.read()
    headers = {"Accept": "application/json", "User-Agent": "DBMS-Diagnostic-FaultRunner/1"}
    if body:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body or None, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = response.status
            raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        status = exc.code
        try:
            raw = exc.read(MAX_HTTP_RESPONSE_BYTES + 1)
        finally:
            exc.close()
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        print(type(exc).__name__, file=sys.stderr)
        return WORKER_NETWORK_ERROR
    sys.stdout.buffer.write(f"{status}\n".encode("ascii"))
    sys.stdout.buffer.write(raw)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) != 4 or values[0] != "--worker":
        return 64
    try:
        timeout_seconds = float(values[3])
    except ValueError:
        return 64
    return _worker_request(values[1], values[2], timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
