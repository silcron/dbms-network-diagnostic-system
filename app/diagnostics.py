"""DBMS probing and OSI trace helpers."""

from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timezone
from typing import Union

from app.db_probe import probe_live_database
from app.diagnostic_models import DemoDiagnosticResult, LiveDiagnosticResult


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def format_mac() -> str:
    raw = uuid.getnode()
    return ":".join(f"{(raw >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


DEMO_SCENARIOS = {"normal", "warning", "down"}


class InvalidDiagnosticModeError(ValueError):
    def __init__(self) -> None:
        super().__init__("mode must be auto, live, or demo")


def probe_database(
    demo_scenario: str = "normal", mode: str = "auto"
) -> Union[DemoDiagnosticResult, LiveDiagnosticResult]:
    """Run a real probe or a clearly labelled simulation."""
    if mode not in {"auto", "live", "demo"}:
        raise InvalidDiagnosticModeError
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

    return probe_live_database()


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
