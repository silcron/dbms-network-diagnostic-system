"""Verify saved Wireshark evidence without changing either capture file."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import NamedTuple

from scripts.packet_analysis import analyze_capture
from scripts.packet_parsing import JsonObject, PacketDataError, decode_framed_messages
from scripts.packet_process import BoundedCommandError, run_bounded
from scripts.packet_report import report_lines
from scripts.packet_tshark import CaptureRequest, TsharkRunner


ROOT = Path(__file__).resolve().parents[1]
DEMO_CAPTURE = ROOT / "demo_tcp_9090_20261005.pcapng"
LIVE_CAPTURE = ROOT / "live_mariadb_3307_20261005.pcapng"


class FixtureFieldQuery(NamedTuple):
    capture: Path
    display_filter: str
    fields: tuple[str, ...]


class FixtureHandshake(NamedTuple):
    stream: int
    frames: tuple[int, int, int]
    port: int


def tshark(executable: str, query: FixtureFieldQuery) -> list[list[str]]:
    command = [
        executable,
        "-n",
        "-r",
        str(query.capture),
        "-d",
        "tcp.port==3307,mysql",
        "-Y",
        query.display_filter,
        "-T",
        "fields",
    ]
    for field in query.fields:
        command.extend(("-e", field))
    result = run_bounded(command)
    try:
        output = result.stdout.decode("utf-8-sig").strip()
    except UnicodeDecodeError as exc:
        raise PacketDataError("field_encoding", "TShark fields are not UTF-8") from exc
    return [line.split("\t") for line in output.splitlines()] if output else []


def check_handshake(
    executable: str,
    capture: Path,
    expected: FixtureHandshake,
) -> None:
    filter_text = " || ".join(
        f"frame.number=={number}" for number in expected.frames
    )
    rows = tshark(
        executable,
        FixtureFieldQuery(
            capture,
            filter_text,
            (
                "frame.number",
                "tcp.stream",
                "tcp.srcport",
                "tcp.dstport",
                "tcp.flags.syn",
                "tcp.flags.ack",
                "tcp.seq_raw",
                "tcp.ack_raw",
            ),
        ),
    )
    if len(rows) != 3 or [int(row[0]) for row in rows] != list(expected.frames):
        raise PacketDataError(
            "fixture_handshake",
            f"{capture.name}: handshake 패킷 번호가 맞지 않습니다: {expected.frames}",
        )
    expected_flags = (("True", "False"), ("True", "True"), ("False", "True"))
    for row, (syn, ack) in zip(rows, expected_flags):
        if int(row[1]) != expected.stream or (row[4], row[5]) != (syn, ack):
            raise PacketDataError(
                "fixture_handshake",
                f"{capture.name}: frame {row[0]}의 스트림 또는 TCP 플래그가 다릅니다.",
            )
    port = expected.port
    if int(rows[0][3]) != port or int(rows[1][2]) != port or int(rows[2][3]) != port:
        raise PacketDataError(
            "fixture_handshake",
            f"{capture.name}: TCP {port} handshake 방향이 맞지 않습니다.",
        )
    syn_sequence = int(rows[0][6])
    syn_ack_sequence = int(rows[1][6])
    if int(rows[1][7]) != (syn_sequence + 1) % (2**32):
        raise PacketDataError(
            "fixture_handshake",
            f"{capture.name}: SYN/ACK 확인 번호가 SYN 순서 번호와 맞지 않습니다.",
        )
    if int(rows[2][6]) != (syn_sequence + 1) % (2**32):
        raise PacketDataError(
            "fixture_handshake",
            f"{capture.name}: ACK 순서 번호가 SYN 순서 번호와 맞지 않습니다.",
        )
    if int(rows[2][7]) != (syn_ack_sequence + 1) % (2**32):
        raise PacketDataError(
            "fixture_handshake",
            f"{capture.name}: ACK 확인 번호가 SYN/ACK 순서 번호와 맞지 않습니다.",
        )
    frame_text = ", ".join(map(str, expected.frames))
    print(
        f"OK  {capture.name}: TCP {port} stream {expected.stream} "
        f"SYN → SYN/ACK → ACK ({frame_text})"
    )


def response_json(executable: str, capture: Path, frame: int) -> JsonObject:
    rows = tshark(
        executable,
        FixtureFieldQuery(capture, f"frame.number=={frame}", ("tcp.payload",)),
    )
    if len(rows) != 1 or not rows[0][0]:
        raise PacketDataError(
            "fixture_payload",
            f"{capture.name}: frame {frame}에 TCP 응용 데이터가 없습니다.",
        )
    payload = bytes.fromhex(rows[0][0].replace(":", ""))
    messages = decode_framed_messages(payload)
    if len(messages) != 1:
        raise PacketDataError(
            "fixture_payload",
            f"{capture.name}: 지정한 프레임에 완전한 응답 하나가 없습니다.",
        )
    return messages[0]


def check_known_fixtures(executable: str) -> None:
    check_handshake(
        executable, DEMO_CAPTURE, FixtureHandshake(0, (1, 2, 3), 9090)
    )
    demo = response_json(executable, DEMO_CAPTURE, 10)
    demo_result = demo.get("result", {})
    osi_trace = demo.get("osi_trace", [])
    if not isinstance(demo_result, dict) or not isinstance(osi_trace, list):
        raise PacketDataError("fixture_demo", "DEMO 응답의 구조가 맞지 않습니다.")
    demo_status = (demo_result.get("mode"), demo_result.get("status"))
    if demo_status != ("DEMO", "NORMAL") or len(osi_trace) != 7:
        raise PacketDataError(
            "fixture_demo", "DEMO 응답의 상태 또는 OSI 7계층 기록이 맞지 않습니다."
        )
    print("OK  DEMO/NORMAL 응답: 모의 결과와 OSI 7계층 확인")

    check_handshake(
        executable, LIVE_CAPTURE, FixtureHandshake(0, (1, 2, 3), 9090)
    )
    check_handshake(
        executable, LIVE_CAPTURE, FixtureHandshake(1, (10, 11, 12), 3307)
    )
    version = tshark(
        executable,
        FixtureFieldQuery(LIVE_CAPTURE, "frame.number==13", ("mysql.version",)),
    )
    if len(version) != 1 or not version[0][0].startswith("11.8.9-MariaDB"):
        raise PacketDataError(
            "fixture_database",
            "MariaDB 11.8.9 서버 인사 메시지를 확인하지 못했습니다.",
        )
    tls = tshark(
        executable,
        FixtureFieldQuery(
            LIVE_CAPTURE, "tcp.stream==1 && tls.record", ("frame.number",)
        ),
    )
    if not tls:
        raise PacketDataError("fixture_tls", "MariaDB 연결의 TLS 패킷을 확인하지 못했습니다.")
    tls13 = tshark(
        executable,
        FixtureFieldQuery(
            LIVE_CAPTURE,
            "tcp.stream==1 && tls.handshake.type==2 && "
            "tls.handshake.extensions.supported_version==0x0304",
            ("frame.number",),
        ),
    )
    if not tls13:
        raise PacketDataError(
            "fixture_tls",
            "MariaDB 연결에서 서버가 선택한 TLS 1.3을 확인하지 못했습니다.",
        )
    live = response_json(executable, LIVE_CAPTURE, 59)
    result = live.get("result", {})
    live_osi_trace = live.get("osi_trace", [])
    if not isinstance(result, dict) or not isinstance(live_osi_trace, list):
        raise PacketDataError("fixture_live", "LIVE 응답의 구조가 맞지 않습니다.")
    live_status = (result.get("mode"), result.get("status"))
    if live_status != ("LIVE", "NORMAL") or len(live_osi_trace) != 7:
        raise PacketDataError(
            "fixture_live", "LIVE 응답의 상태 또는 OSI 7계층 기록이 맞지 않습니다."
        )
    if "database_connect_ms" not in result or "database_rtt_ms" not in result:
        raise PacketDataError("fixture_live", "LIVE 응답에 DB 연결·전체 지연시간이 없습니다.")
    print(
        f"OK  MariaDB {version[0][0]} 인사, TLS 1.3 협상, "
        f"TLS 패킷 {len(tls)}개, LIVE/NORMAL 응답 (59)"
    )
    print("패킷 증거 검증 완료. 루프백 캡처는 물리 케이블·무선 신호를 증명하지 않습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="저장된 TCP/MariaDB 패킷 증거를 검증합니다.")
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--diagnostic-port", type=int)
    parser.add_argument("--db-port", type=int, default=3307)
    parser.add_argument(
        "--tshark",
        default=shutil.which("tshark") or r"C:\Program Files\Wireshark\tshark.exe",
    )
    args = parser.parse_args()
    if (args.capture is None) != (args.diagnostic_port is None):
        parser.error("--capture and --diagnostic-port must be provided together")

    try:
        if args.capture is None:
            check_known_fixtures(args.tshark)
        else:
            report = analyze_capture(
                CaptureRequest(args.capture, args.diagnostic_port, args.db_port),
                TsharkRunner(args.tshark),
            )
            for line in report_lines(report):
                print(line)
    except (BoundedCommandError, PacketDataError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
