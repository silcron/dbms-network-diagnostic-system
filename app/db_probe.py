from __future__ import annotations

import math
import os
import socket
import time
from contextlib import suppress
from types import TracebackType
from typing import Dict, Final, Literal, Optional, Protocol, Tuple, Type

from app.diagnostic_models import DbConfig, ErrorKind, LiveDiagnosticResult, ProbeFacts, ProbeOutcome, Status


class _Cursor(Protocol):
    def __enter__(self) -> "_Cursor": ...

    def __exit__(
        self,
        exception_type: Optional[Type[BaseException]],
        exception: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> Optional[bool]: ...

    def execute(self, statement: str) -> int: ...

    def fetchone(self) -> Optional[Tuple[int, ...]]: ...


class _Connection(Protocol):
    def cursor(self) -> _Cursor: ...

    def close(self) -> None: ...


class _DbConfigError(ValueError):
    def __init__(self, setting: str, database: str) -> None:
        self.setting = setting
        self.database = database
        super().__init__(setting)


_DEFAULT_TIMEOUT_SECONDS: Final = 3.0
_MAX_TIMEOUT_SECONDS: Final = 30.0
_DEFAULT_WARNING_MS: Final = 500.0
_NETWORK_ERRNOS: Final = frozenset({2002, 2003, 2005, 2006, 2013})
_TIMEOUT_SETTINGS: Final = (
    "DB_CONNECT_TIMEOUT_SECONDS",
    "DB_READ_TIMEOUT_SECONDS",
    "DB_WRITE_TIMEOUT_SECONDS",
)
_ERROR_STATUS: Final[Dict[ErrorKind, Status]] = {
    "none": "NORMAL",
    "config": "WARNING",
    "dependency": "WARNING",
    "network": "DOWN",
    "timeout": "DOWN",
    "auth": "WARNING",
    "database": "WARNING",
    "query": "WARNING",
    "internal": "WARNING",
}
_ERROR_DETAIL: Final[Dict[Tuple[str, ErrorKind], str]] = {
    ("connect", "network"): "DBMS 네트워크 연결에 실패했습니다. 주소·포트·서비스 상태를 확인하세요.",
    ("query", "network"): "SELECT 1 실행 중 DBMS 연결이 끊어졌습니다. 네트워크와 서비스 상태를 확인하세요.",
    ("connect", "timeout"): "DBMS 연결 시간이 제한을 넘었습니다. 네트워크와 DBMS 상태를 확인하세요.",
    ("query", "timeout"): "SELECT 1 응답 시간이 읽기 제한을 넘었습니다. DBMS 상태를 확인하세요.",
    ("connect", "auth"): "DBMS 인증에 실패했습니다. 사용자 이름과 권한 설정을 확인하세요.",
    ("query", "auth"): "SELECT 1 실행 중 DBMS 인증 오류가 발생했습니다. 권한 설정을 확인하세요.",
    ("connect", "database"): "요청한 데이터베이스를 사용할 수 없습니다. DB_NAME과 권한 설정을 확인하세요.",
    ("query", "database"): "SELECT 1 실행 중 데이터베이스 오류가 발생했습니다. DBMS 상태를 확인하세요.",
    ("query", "query"): "SELECT 1 실행 결과를 확인할 수 없습니다. 쿼리 권한과 DBMS 상태를 확인하세요.",
    ("connect", "internal"): "DBMS 연결 진단 중 내부 오류가 발생했습니다.",
    ("query", "internal"): "SELECT 1 진단 중 내부 오류가 발생했습니다.",
}
_CONFIG_DETAIL: Final[Dict[str, str]] = {
    "DB_HOST": "실제 진단을 위한 DB_HOST가 설정되지 않았습니다. 모의 결과로 대체하지 않았습니다.",
    "DB_PORT": "DB_PORT 설정이 잘못되었습니다. 1~65535 범위의 포트를 입력하세요.",
    "DB_CONNECT_TIMEOUT_SECONDS": "DB_CONNECT_TIMEOUT_SECONDS는 0초 초과 30초 이하의 유한한 숫자여야 합니다.",
    "DB_READ_TIMEOUT_SECONDS": "DB_READ_TIMEOUT_SECONDS는 0초 초과 30초 이하의 유한한 숫자여야 합니다.",
    "DB_WRITE_TIMEOUT_SECONDS": "DB_WRITE_TIMEOUT_SECONDS는 0초 초과 30초 이하의 유한한 숫자여야 합니다.",
    "DB_WARNING_MS": "DB_WARNING_MS는 0보다 큰 유한한 숫자여야 합니다.",
}


def _load_config() -> DbConfig:
    raw_host = os.getenv("DB_HOST")
    if raw_host is None or not raw_host.strip():
        raise _DbConfigError("DB_HOST", "MariaDB (미설정)")
    host = raw_host.strip()
    database = f"MariaDB ({host})"
    try:
        port = int(os.getenv("DB_PORT", "3306"))
    except ValueError as error:
        raise _DbConfigError("DB_PORT", database) from error
    if not 1 <= port <= 65535:
        raise _DbConfigError("DB_PORT", database)

    timeout_values: Dict[str, float] = {}
    for setting in _TIMEOUT_SETTINGS:
        try:
            value = float(os.getenv(setting, str(_DEFAULT_TIMEOUT_SECONDS)))
        except ValueError as error:
            raise _DbConfigError(setting, database) from error
        if not math.isfinite(value) or value <= 0 or value > _MAX_TIMEOUT_SECONDS:
            raise _DbConfigError(setting, database)
        timeout_values[setting] = value

    try:
        warning_ms = float(os.getenv("DB_WARNING_MS", str(_DEFAULT_WARNING_MS)))
    except ValueError as error:
        raise _DbConfigError("DB_WARNING_MS", database) from error
    if not math.isfinite(warning_ms) or warning_ms <= 0:
        raise _DbConfigError("DB_WARNING_MS", database)

    return DbConfig(
        host=host,
        port=port,
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        database_name=os.getenv("DB_NAME") or None,
        connect_timeout=timeout_values["DB_CONNECT_TIMEOUT_SECONDS"],
        read_timeout=timeout_values["DB_READ_TIMEOUT_SECONDS"],
        write_timeout=timeout_values["DB_WRITE_TIMEOUT_SECONDS"],
        warning_ms=warning_ms,
    )


def _elapsed_ms(started: float) -> float:
    elapsed = (time.perf_counter() - started) * 1000.0
    if not math.isfinite(elapsed) or elapsed < 0:
        return 0.0
    return round(elapsed, 2)


def _db_errno(error: BaseException) -> Optional[int]:
    if not error.args:
        return None
    value = error.args[0]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _is_timeout(error: BaseException, inspect_message: bool) -> bool:
    current: Optional[BaseException] = error
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, (TimeoutError, socket.timeout)):
            return True
        if inspect_message:
            message = " ".join(str(argument) for argument in current.args).casefold()
            if "(timed out)" in message:
                return True
        current = current.__cause__ or current.__context__
    return False


def _classify(error: BaseException, phase: Literal["connect", "query"]) -> Tuple[ErrorKind, Optional[int]]:
    errno = _db_errno(error)
    if errno == 1045:
        return "auth", errno
    if errno == 1049:
        return "database", errno
    if errno in _NETWORK_ERRNOS:
        return ("timeout" if _is_timeout(error, True) else "network"), errno
    if isinstance(error, OSError):
        return ("timeout" if _is_timeout(error, True) else "network"), errno
    if errno is not None:
        return ("query" if phase == "query" else "database"), errno
    if _is_timeout(error, False):
        return "timeout", None
    return "internal", None


def _build_result(database: str, outcome: ProbeOutcome, total_ms: float) -> LiveDiagnosticResult:
    result: LiveDiagnosticResult = {
        "mode": "LIVE",
        "scenario": "live",
        "status": outcome.status,
        "database": database,
        "detail": outcome.detail,
        "database_rtt_ms": total_ms,
        "failed_phase": outcome.failed_phase,
        "error_kind": outcome.error_kind,
        "db_errno": outcome.db_errno,
        "query_ms": outcome.query_ms,
        "total_ms": total_ms,
    }
    if outcome.connect_ms is not None:
        result["database_connect_ms"] = outcome.connect_ms
    return result


def probe_live_database() -> LiveDiagnosticResult:
    started = time.perf_counter()
    try:
        config = _load_config()
    except _DbConfigError as error:
        total_ms = _elapsed_ms(started)
        outcome = ProbeOutcome("WARNING", _CONFIG_DETAIL[error.setting], "config", "config", None, None, None)
        return _build_result(error.database, outcome, total_ms)

    database = f"MariaDB ({config.host})"
    try:
        import pymysql
    except ImportError:
        total_ms = _elapsed_ms(started)
        outcome = ProbeOutcome(
            "WARNING",
            "PyMySQL이 설치되지 않아 실제 DBMS 접속을 확인할 수 없습니다.",
            "dependency",
            "dependency",
            None,
            None,
            None,
        )
        return _build_result(database, outcome, total_ms)

    connection: Optional[_Connection] = None
    connect_started = time.perf_counter()
    try:
        try:
            connection = pymysql.connect(
                host=config.host,
                port=config.port,
                user=config.user,
                password=config.password,
                database=config.database_name,
                connect_timeout=config.connect_timeout,
                read_timeout=config.read_timeout,
                write_timeout=config.write_timeout,
            )
        except Exception as error:  # noqa: BROAD_EXCEPT_OK
            error_kind, errno = _classify(error, "connect")
            facts = ProbeFacts(False, "connect", error_kind, errno, _elapsed_ms(connect_started), None)
        else:
            connect_ms = _elapsed_ms(connect_started)
            query_started = time.perf_counter()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    query_result = cursor.fetchone()
            except Exception as error:  # noqa: BROAD_EXCEPT_OK
                error_kind, errno = _classify(error, "query")
                facts = ProbeFacts(False, "query", error_kind, errno, connect_ms, _elapsed_ms(query_started))
            else:
                query_ms = _elapsed_ms(query_started)
                if not query_result or query_result[0] != 1:
                    facts = ProbeFacts(False, "query", "query", None, connect_ms, query_ms)
                else:
                    facts = ProbeFacts(True, None, "none", None, connect_ms, query_ms)
    finally:
        if connection is not None:
            with suppress(Exception):
                connection.close()

    total_ms = _elapsed_ms(started)
    if facts.successful:
        slow = total_ms > config.warning_ms
        status: Status = "WARNING" if slow else "NORMAL"
        detail = (
            f"TCP 연결·로그인·SELECT 1은 성공했지만, 전체 응답이 {config.warning_ms:g} ms를 넘었습니다."
            if slow
            else "TCP 연결·DB 로그인·SELECT 1 쿼리가 모두 성공했습니다."
        )
    else:
        status = _ERROR_STATUS[facts.error_kind]
        detail = _ERROR_DETAIL.get(
            (facts.failed_phase or "connect", facts.error_kind),
            "DBMS 진단 중 내부 오류가 발생했습니다.",
        )
    outcome = ProbeOutcome(
        status,
        detail,
        facts.failed_phase,
        facts.error_kind,
        facts.db_errno,
        facts.connect_ms,
        facts.query_ms,
    )
    return _build_result(database, outcome, total_ms)
