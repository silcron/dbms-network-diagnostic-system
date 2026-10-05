"""Verify saved Wireshark evidence without changing either capture file."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEMO_CAPTURE = ROOT / "tcp_9090_loopback_capture.pcapng"
LIVE_CAPTURE = ROOT / "live_mariadb_tcp_capture.pcapng"


def tshark(executable: str, capture: Path, display_filter: str, *fields: str) -> list[list[str]]:
    command = [executable, "-n", "-r", str(capture), "-Y", display_filter, "-T", "fields"]
    for field in fields:
        command.extend(("-e", field))
    output = subprocess.check_output(command, text=True, encoding="utf-8").strip()
    return [line.split("\t") for line in output.splitlines()] if output else []


def check_handshake(executable: str, capture: Path, stream: int, frames: tuple[int, int, int], port: int) -> None:
    filter_text = " || ".join(f"frame.number=={number}" for number in frames)
    rows = tshark(
        executable, capture, filter_text,
        "frame.number", "tcp.stream", "tcp.srcport", "tcp.dstport", "tcp.flags.syn", "tcp.flags.ack",
    )
    if len(rows) != 3 or [int(row[0]) for row in rows] != list(frames):
        raise RuntimeError(f"{capture.name}: handshake 패킷 번호가 맞지 않습니다: {frames}")
    expected_flags = (("True", "False"), ("True", "True"), ("False", "True"))
    for row, (syn, ack) in zip(rows, expected_flags):
        if int(row[1]) != stream or (row[4], row[5]) != (syn, ack):
            raise RuntimeError(f"{capture.name}: frame {row[0]}의 스트림 또는 TCP 플래그가 다릅니다.")
    if int(rows[0][3]) != port or int(rows[1][2]) != port or int(rows[2][3]) != port:
        raise RuntimeError(f"{capture.name}: TCP {port} handshake 방향이 맞지 않습니다.")
    print(f"OK  {capture.name}: TCP {port} stream {stream} SYN → SYN/ACK → ACK ({', '.join(map(str, frames))})")


def response_json(executable: str, capture: Path, frame: int) -> dict:
    rows = tshark(executable, capture, f"frame.number=={frame}", "tcp.payload")
    if len(rows) != 1 or not rows[0][0]:
        raise RuntimeError(f"{capture.name}: frame {frame}에 TCP 응용 데이터가 없습니다.")
    payload = bytes.fromhex(rows[0][0].replace(":", ""))
    if len(payload) < 4:
        raise RuntimeError(f"{capture.name}: 4바이트 길이 헤더가 없습니다.")
    message_length = int.from_bytes(payload[:4], "big")
    if message_length != len(payload) - 4:
        raise RuntimeError(f"{capture.name}: 길이 헤더와 JSON 데이터 길이가 다릅니다.")
    return json.loads(payload[4:].decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="저장된 TCP/MariaDB 패킷 증거를 검증합니다.")
    parser.add_argument("--tshark", default=shutil.which("tshark") or r"C:\Program Files\Wireshark\tshark.exe")
    args = parser.parse_args()
    if not Path(args.tshark).is_file():
        raise SystemExit("TShark 실행 파일을 찾지 못했습니다. Wireshark 설치 경로를 --tshark로 지정하세요.")

    check_handshake(args.tshark, DEMO_CAPTURE, 0, (1, 2, 3), 9090)
    demo = response_json(args.tshark, DEMO_CAPTURE, 10)
    if demo.get("result", {}).get("mode") != "DEMO" or len(demo.get("osi_trace", [])) != 7:
        raise RuntimeError("DEMO 응답의 모드 또는 OSI 7계층 기록이 맞지 않습니다.")
    print("OK  DEMO 응답: 모의 결과와 OSI 7계층 확인")

    check_handshake(args.tshark, LIVE_CAPTURE, 0, (1, 2, 3), 9090)
    check_handshake(args.tshark, LIVE_CAPTURE, 1, (10, 11, 12), 3306)
    version = tshark(args.tshark, LIVE_CAPTURE, "frame.number==13", "mysql.version")
    if len(version) != 1 or not version[0][0].startswith("11.8.9-MariaDB"):
        raise RuntimeError("MariaDB 11.8.9 서버 인사 메시지를 확인하지 못했습니다.")
    tls = tshark(args.tshark, LIVE_CAPTURE, "tcp.stream==1 && tls.record", "frame.number")
    if not tls:
        raise RuntimeError("MariaDB 연결의 TLS 패킷을 확인하지 못했습니다.")
    live = response_json(args.tshark, LIVE_CAPTURE, 58)
    result = live.get("result", {})
    if (result.get("mode"), result.get("status")) != ("LIVE", "NORMAL") or len(live.get("osi_trace", [])) != 7:
        raise RuntimeError("LIVE 응답의 상태 또는 OSI 7계층 기록이 맞지 않습니다.")
    if "database_connect_ms" not in result or "database_rtt_ms" not in result:
        raise RuntimeError("LIVE 응답에 DB 연결·전체 지연시간이 없습니다.")
    print(f"OK  MariaDB {version[0][0]} 인사, TLS 패킷 {len(tls)}개, LIVE/NORMAL 응답")
    print("패킷 증거 검증 완료. 루프백 캡처는 물리 케이블·무선 신호를 증명하지 않습니다.")


if __name__ == "__main__":
    main()
