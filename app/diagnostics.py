"""DBMS probing and OSI trace helpers."""

from __future__ import annotations

import os
import socket
import time
import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_mac() -> str:
    raw = uuid.getnode()
    return ":".join(f"{(raw >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


DEMO_SCENARIOS = {"normal", "warning", "down"}
DB_TIMEOUT_SECONDS = 3
DB_WARNING_MS = 500


def probe_database(demo_scenario: str = "normal", mode: str = "auto") -> dict[str, Any]:
    """Run a real probe or a clearly labelled simulation."""
    if mode not in {"auto", "live", "demo"}:
        raise ValueError("mode must be auto, live, or demo")
    host = os.getenv("DB_HOST")
    if mode == "demo" or (mode == "auto" and not host):
        scenario = demo_scenario if demo_scenario in DEMO_SCENARIOS else "normal"
        demo_results = {
            "normal": {
                "status": "NORMAL",
                "detail": "모의 진단: DBMS 연결과 응답이 정상인 상황입니다. 실제 DB 연결은 확인하지 않았습니다.",
            },
            "warning": {
                "status": "WARNING",
                "detail": "모의 진단: DBMS 응답 지연이 경고 기준을 넘은 상황입니다. 실제 DB 연결은 확인하지 않았습니다.",
            },
            "down": {
                "status": "DOWN",
                "detail": "모의 진단: DBMS 접속이 거부된 상황입니다. 실제 DB 연결은 확인하지 않았습니다.",
            },
        }
        return {
            "mode": "DEMO",
            "scenario": scenario,
            **demo_results[scenario],
            "database": "MariaDB (모의 시연)",
        }

    if not host:
        return {
            "mode": "LIVE",
            "scenario": "live",
            "status": "WARNING",
            "database": "MariaDB (미설정)",
            "detail": "실제 진단을 위한 DB_HOST가 설정되지 않았습니다. 모의 결과로 대체하지 않았습니다.",
        }

    started = time.perf_counter()
    database = f"MariaDB ({host})"
    try:
        port = int(os.getenv("DB_PORT", "3306"))
        if not 1 <= port <= 65535:
            raise ValueError("DB_PORT must be between 1 and 65535")
    except ValueError:
        return {
            "mode": "LIVE",
            "scenario": "live",
            "status": "WARNING",
            "database": database,
            "detail": "DB_PORT 설정이 잘못되었습니다. 1~65535 범위의 포트를 입력하세요.",
        }

    try:
        import pymysql  # optional dependency
    except ImportError:
        return {
            "mode": "LIVE",
            "scenario": "live",
            "status": "WARNING",
            "database": database,
            "detail": "PyMySQL이 설치되지 않아 실제 DBMS 접속을 확인할 수 없습니다.",
        }

    connection = None
    database_connect_ms = None
    try:
        connection = pymysql.connect(
            host=host,
            port=port,
            user=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", ""),
            database=os.getenv("DB_NAME") or None,
            connect_timeout=DB_TIMEOUT_SECONDS,
            read_timeout=DB_TIMEOUT_SECONDS,
            write_timeout=DB_TIMEOUT_SECONDS,
        )
        database_connect_ms = round((time.perf_counter() - started) * 1000, 2)
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            query_result = cursor.fetchone()
        if not query_result or query_result[0] != 1:
            raise ValueError("SELECT 1 returned an unexpected result")
        database_rtt_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "mode": "LIVE",
            "scenario": "live",
            "status": "WARNING" if database_rtt_ms > DB_WARNING_MS else "NORMAL",
            "database": database,
            "detail": (
                f"TCP 연결·로그인·SELECT 1은 성공했지만, 전체 응답이 {DB_WARNING_MS} ms를 넘었습니다."
                if database_rtt_ms > DB_WARNING_MS else "TCP 연결·DB 로그인·SELECT 1 쿼리가 모두 성공했습니다."
            ),
            "database_connect_ms": database_connect_ms,
            "database_rtt_ms": database_rtt_ms,
        }
    except Exception as exc:  # database connection errors are a diagnostic result
        error_code = exc.args[0] if exc.args else None
        network_failure = isinstance(exc, OSError) or error_code in {2002, 2003, 2005, 2006, 2013}
        result = {
            "mode": "LIVE",
            "scenario": "live",
            "status": "DOWN" if network_failure else "WARNING",
            "database": database,
            "detail": (
                f"DBMS 네트워크 연결 실패({type(exc).__name__}). 주소·포트·서비스 상태를 확인하세요."
                if network_failure else f"DB 로그인 또는 SELECT 1 실행 실패({type(exc).__name__})."
            ),
            "database_rtt_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        if database_connect_ms is not None:
            result["database_connect_ms"] = database_connect_ms
        return result
    finally:
        if connection is not None:
            connection.close()


def build_osi_trace(peer_ip: str, server_ip: str, server_port: int, session_id: str) -> list[dict[str, str]]:
    loopback = peer_ip.startswith("127.") and server_ip.startswith("127.")
    data_link = (
        "루프백 인터페이스를 사용하므로 이 경로에는 이더넷 MAC 프레임이 없습니다."
        if loopback else f"로컬 인터페이스 MAC {format_mac()} (참고 정보)"
    )
    physical = (
        "가상 루프백 경로이므로 케이블·무선 전파를 통한 실제 전송은 없습니다."
        if loopback else "활성 인터페이스에 따라 이더넷 케이블 또는 Wi-Fi 전파로 전송됩니다."
    )
    return [
        {"layer": "7 응용 계층", "application": "진단 요청·응답(hello/diagnose/logout); 응용 메시지에 4바이트 길이 헤더를 붙여 경계를 구분합니다."},
        {"layer": "6 표현 계층", "application": "진단 데이터를 JSON으로 직렬화하고 UTF-8로 인코딩합니다."},
        {"layer": "5 세션 계층", "application": f"서버가 발급한 세션 ID {session_id[:8]}…을 요청마다 확인합니다(응용 프로그램의 세션 기능)."},
        {"layer": "4 전송 계층", "application": f"TCP 소켓으로 연결하고 목적지 포트 {server_port}을 사용합니다."},
        {"layer": "3 네트워크 계층", "application": f"IPv4 출발지 {peer_ip} → 목적지 {server_ip}"},
        {"layer": "2 데이터링크 계층", "application": data_link},
        {"layer": "1 물리 계층", "application": physical},
    ]


def local_ipv4() -> str:
    try:
        return socket.gethostbyname(socket.gethostname())
    except socket.gaierror:
        return "127.0.0.1"
