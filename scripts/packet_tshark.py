from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Protocol

from scripts.packet_parsing import (
    TSHARK_FIELDS,
    FollowStream,
    PacketDataError,
    TcpPacket,
    parse_follow_raw,
    parse_tshark_fields,
)
from scripts.packet_process import CommandLimits, DEFAULT_COMMAND_LIMITS, run_bounded


class CaptureRequest(NamedTuple):
    capture: Path
    diagnostic_port: int
    db_port: int


class FollowQuery(NamedTuple):
    capture: Path
    stream: int
    decoded_limit: int


class PacketRunner(Protocol):
    def packets(self, request: CaptureRequest) -> tuple[TcpPacket, ...]: ...

    def follow(self, query: FollowQuery) -> FollowStream: ...


class TsharkRunner:
    __slots__ = ("_executable", "_limits")

    def __init__(
        self,
        executable: str,
        limits: CommandLimits = DEFAULT_COMMAND_LIMITS,
    ) -> None:
        self._executable = executable
        self._limits = limits

    def packets(self, request: CaptureRequest) -> tuple[TcpPacket, ...]:
        display_filter = (
            f"tcp.port == {request.diagnostic_port} || tcp.port == {request.db_port}"
        )
        command = [
            self._executable,
            "-n",
            "-2",
            "-r",
            str(request.capture),
            "-d",
            f"tcp.port=={request.db_port},mysql",
            "-Y",
            display_filter,
            "-T",
            "fields",
            "-E",
            "header=y",
            "-E",
            "separator=/t",
            "-E",
            "occurrence=f",
        ]
        for field in TSHARK_FIELDS:
            command.extend(("-e", field))
        result = run_bounded(command, self._limits)
        try:
            text = result.stdout.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise PacketDataError("field_encoding", "TShark fields are not UTF-8") from exc
        return parse_tshark_fields(text)

    def follow(self, query: FollowQuery) -> FollowStream:
        result = run_bounded(
            [
                self._executable,
                "-n",
                "-r",
                str(query.capture),
                "-q",
                "-z",
                f"follow,tcp,raw,{query.stream}",
            ],
            self._limits,
        )
        try:
            text = result.stdout.decode("ascii")
        except UnicodeDecodeError as exc:
            raise PacketDataError("follow_encoding", "raw follow output is not ASCII") from exc
        return parse_follow_raw(text, query.stream, query.decoded_limit)
