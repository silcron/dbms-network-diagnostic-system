"""Check the locally running course demonstration before presenting."""

from __future__ import annotations

import argparse
from urllib.request import urlopen

from app.client_core import DiagnosticClient


class PresentationCheckError(RuntimeError):
    pass


def check_tcp(host: str, port: int, mode: str, scenario: str, expected: str, require_current: bool = False) -> None:
    client = DiagnosticClient(host, port)
    try:
        hello = client.connect()
        if require_current and hello.get("protocol_version") != 2:
            raise PresentationCheckError(
                f"TCP {port}: 구버전 서버가 실행 중입니다. 과제용 TCP 서버를 종료한 뒤 다시 시작하세요."
            )
        response = client.diagnose(scenario, mode=mode)
        if response.get("type") != "diagnostic_result":
            raise PresentationCheckError(f"TCP {port}: 진단 응답이 아닙니다: {response.get('type')}")
        result = response["result"]
        if result.get("mode") != mode.upper() or result.get("status") != expected:
            raise PresentationCheckError(
                f"TCP {port}: 기대 {mode.upper()}/{expected}, 실제 {result.get('mode')}/{result.get('status')}"
            )
        if len(response.get("osi_trace", [])) != 7:
            raise PresentationCheckError(f"TCP {port}: OSI 추적이 7계층이 아닙니다.")
        print(f"정상  TCP {port}  {mode.upper():4}  {expected:7}  OSI 7계층")
    finally:
        client.logout()


def main() -> None:
    parser = argparse.ArgumentParser(description="발표 전 웹 화면과 TCP 진단 결과를 점검합니다.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--live-port", type=int, default=9090)
    parser.add_argument("--demo-port", type=int, default=9090)
    parser.add_argument("--demo-only", action="store_true", help="LIVE DB 연결 없이 DEMO 결과만 점검합니다.")
    parser.add_argument("--require-current-server", action="store_true")
    args = parser.parse_args()

    with urlopen(f"http://{args.host}:{args.web_port}/", timeout=5) as response:
        page = response.read().decode("utf-8")
    if "통신 경로" not in page or "OSI 7계층" not in page:
        raise PresentationCheckError("웹 화면에 통신 경로 또는 OSI 7계층 설명이 없습니다.")
    print(f"정상  웹 화면  http://{args.host}:{args.web_port}/")

    if not args.demo_only:
        check_tcp(args.host, args.live_port, "live", "normal", "NORMAL", args.require_current_server)
    for scenario, expected in (("normal", "NORMAL"), ("warning", "WARNING"), ("down", "DOWN")):
        check_tcp(args.host, args.demo_port, "demo", scenario, expected, args.require_current_server)
    if args.demo_only:
        print("발표 시연 준비 완료: DEMO 3개, 각 결과의 OSI 7계층 확인")
    else:
        print("발표 시연 준비 완료: LIVE 1개, DEMO 3개, 각 결과의 OSI 7계층 확인")


if __name__ == "__main__":
    main()
