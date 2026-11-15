"""Tkinter GUI for the DBMS diagnostic communication system."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from math import isfinite
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable, Dict, List, Union

from .client_core import DiagnosticClient
from .feedback import batch_summary, target_feedback

JsonValue = Union[str, int, float, bool, None, List["JsonValue"], Dict[str, "JsonValue"]]
JsonObject = Dict[str, JsonValue]
EventPayload = Union[JsonObject, Exception, None]

_PHASE_NAMES = {"config": "설정", "dependency": "의존성", "connect": "DB 연결·인증", "query": "SELECT 1"}
_ERROR_NAMES = {
    "config": "설정",
    "dependency": "의존성",
    "network": "네트워크",
    "timeout": "시간 초과",
    "auth": "인증",
    "database": "데이터베이스",
    "query": "쿼리",
    "internal": "내부 처리",
}


class PresentationError(ValueError):
    field: str

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"invalid diagnostic field: {field}")


def _required_text(values: JsonObject, field: str) -> str:
    value = values.get(field)
    if not isinstance(value, str):
        raise PresentationError(field)
    return value


def _finite_nonnegative(value: JsonValue) -> Union[int, float, None]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if value >= 0 and isfinite(float(value)) else None
    except OverflowError:
        return None


def format_diagnostic_lines(response: JsonObject) -> list[str]:
    result_value = response.get("result")
    trace_value = response.get("osi_trace")
    if not isinstance(result_value, dict):
        raise PresentationError("result")
    if not isinstance(trace_value, list):
        raise PresentationError("osi_trace")
    result: JsonObject = result_value

    status = _required_text(result, "status")
    mode = _required_text(result, "mode")
    database = _required_text(result, "database")
    detail = _required_text(result, "detail")
    client_rtt_ms = _finite_nonnegative(response.get("client_rtt_ms"))
    if client_rtt_ms is None:
        raise PresentationError("client_rtt_ms")

    lines = [
        "[DBMS DIAGNOSTIC]",
        f"상태: {status}  |  모드: {mode}",
        f"대상: {database}",
        f"설명: {detail}",
        f"클라이언트 왕복 시간: {client_rtt_ms} ms",
    ]
    targets = response.get("results")
    if isinstance(targets, list) and len(targets) > 1:
        batch = [target for target in targets if isinstance(target, dict) and type(target.get("target_id")) is int]
        if len(batch) != len(targets):
            raise PresentationError("results")
        lines[1:1] = ["[DB별 상태]", batch_summary(batch), *(_target_line(db) for db in batch), "", "[최고 심각도 대상]"]
    else:
        lines.append(f"다음 점검: {target_feedback(result).next_check}")
    simulated = "모의 " if mode == "DEMO" else ""
    facts: list[str] = []
    for field, label in (("database_connect_ms", "DB 연결·인증"), ("query_ms", "SELECT 1")):
        duration = _finite_nonnegative(result.get(field))
        if duration is not None:
            facts.append(f"{simulated}{label}: {duration} ms")
    total = _finite_nonnegative(result.get("total_ms"))
    if total is not None:
        facts.append(f"{simulated}진단 전체: {total} ms")
    else:
        legacy_total = _finite_nonnegative(result.get("database_rtt_ms"))
        if legacy_total is not None:
            facts.append(f"{simulated}DB 진단 전체: {legacy_total} ms")

    failed_phase = result.get("failed_phase")
    if isinstance(failed_phase, str) and failed_phase in _PHASE_NAMES:
        facts.append(f"{simulated}실패 단계: {_PHASE_NAMES[failed_phase]}")
    error_kind = result.get("error_kind")
    if isinstance(error_kind, str) and error_kind in _ERROR_NAMES:
        facts.append(f"{simulated}오류 원인: {_ERROR_NAMES[error_kind]}")
    db_errno = result.get("db_errno")
    if isinstance(db_errno, int) and not isinstance(db_errno, bool) and db_errno >= 0:
        facts.append(f"{simulated}DB 오류 번호: {db_errno}")
    if facts:
        lines.append("진단 세부: " + " · ".join(facts))

    lines.extend(("", "[OSI 7-LAYER TRACE]"))
    for item in trace_value:
        if not isinstance(item, dict):
            raise PresentationError("osi_trace")
        trace_item: JsonObject = item
        lines.append(f"{_required_text(trace_item, 'layer')}: {_required_text(trace_item, 'application')}")
    return lines


def _target_line(target: JsonObject) -> str:
    feedback = target_feedback(target)
    return (f"DB {target['target_id']} · {_required_text(target, 'status')} · {_required_text(target, 'database')}\n"
            f"  {feedback.finding} | {feedback.next_check}")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DBMS 네트워크 접속 진단 통신 시스템")
        self.geometry("1030x720")
        self.minsize(900, 620)
        self.client: DiagnosticClient | None = None
        self.events: queue.Queue[tuple[str, EventPayload]] = queue.Queue()
        self._build()
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Title.TLabel", font=("Malgun Gothic", 18, "bold"))
        style.configure("Heading.TLabel", font=("Malgun Gothic", 11, "bold"))
        style.configure("Status.TLabel", foreground="#755838")

        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="DBMS 네트워크 접속 진단 통신 시스템", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="TCP 요청·응답과 OSI 7계층 적용을 함께 확인합니다.").pack(anchor="w", pady=(3, 14))

        conn = ttk.LabelFrame(outer, text="서버 연결", padding=10)
        conn.pack(fill="x")
        ttk.Label(conn, text="Host").grid(row=0, column=0, sticky="w")
        self.host_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(conn, textvariable=self.host_var, width=25).grid(row=0, column=1, padx=(6, 16))
        ttk.Label(conn, text="Port").grid(row=0, column=2, sticky="w")
        self.port_var = tk.StringVar(value="9090")
        ttk.Entry(conn, textvariable=self.port_var, width=10).grid(row=0, column=3, padx=(6, 16))
        self.connect_button = ttk.Button(conn, text="연결", command=self.connect)
        self.connect_button.grid(row=0, column=4)
        self.diagnose_button = ttk.Button(conn, text="DBMS 진단", command=self.diagnose, state="disabled")
        self.diagnose_button.grid(row=0, column=5, padx=(8, 0))
        self.logout_button = ttk.Button(conn, text="로그아웃", command=self.logout, state="disabled")
        self.logout_button.grid(row=0, column=6, padx=(8, 0))

        self.status = tk.StringVar(value="서버 연결 전")
        ttk.Label(outer, textvariable=self.status, style="Status.TLabel").pack(anchor="w", pady=(12, 6))
        self.output = scrolledtext.ScrolledText(outer, font=("Consolas", 10), wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True)

    def _write(self, message: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", message.rstrip() + "\n\n")
        self.output.see("end")
        self.output.configure(state="disabled")

    def _run(self, action: Callable[[], Union[JsonObject, None]], event: str) -> None:
        def worker() -> None:
            try:
                self.events.put((event, action()))
            except Exception as exc:  # noqa: BROAD_EXCEPT_OK
                self.events.put(("error", exc))

        threading.Thread(target=worker, daemon=True).start()

    def connect(self) -> None:
        try:
            self.client = DiagnosticClient(self.host_var.get().strip(), int(self.port_var.get().strip()))
        except ValueError:
            messagebox.showerror("입력 오류", "Port에는 숫자를 입력하세요.")
            return
        self.status.set("서버에 연결하는 중…")
        self._run(self.client.connect, "connected")

    def diagnose(self) -> None:
        if self.client:
            self.status.set("DBMS 상태를 진단하는 중…")
            self._run(self.client.diagnose, "diagnosed")

    def logout(self) -> None:
        if self.client:
            self._run(self.client.logout, "logged_out")

    def _drain_events(self) -> None:
        while not self.events.empty():
            event, payload = self.events.get_nowait()
            if event == "connected":
                if not isinstance(payload, dict):
                    self.status.set("오류 발생")
                    self._write("[ERROR] 연결 응답 형식이 잘못되었습니다.")
                    continue
                try:
                    message = _required_text(payload, "message")
                    session_id = _required_text(payload, "session_id")
                except PresentationError as exc:
                    self.status.set("오류 발생")
                    self._write(f"[ERROR] {exc}")
                    continue
                self.status.set("연결됨 · 세션이 생성되었습니다")
                self.connect_button.configure(state="disabled")
                self.diagnose_button.configure(state="normal")
                self.logout_button.configure(state="normal")
                self._write(f"[SESSION]\n{message}\n세션 ID: {session_id[:12]}…")
            elif event == "diagnosed":
                if not isinstance(payload, dict):
                    self.status.set("오류 발생")
                    self._write("[ERROR] 진단 응답 형식이 잘못되었습니다.")
                elif payload.get("type") == "error":
                    message = payload.get("message")
                    self._write(f"[ERROR] {message if isinstance(message, str) else '진단 서버가 요청을 거부했습니다.'}")
                else:
                    try:
                        lines = format_diagnostic_lines(payload)
                    except PresentationError as exc:
                        self.status.set("오류 발생")
                        self._write(f"[ERROR] {exc}")
                    else:
                        self._write("\n".join(lines))
                        self.status.set("진단 완료 · 결과와 OSI 추적 로그를 확인하세요")
            elif event == "logged_out":
                self.client = None
                self.status.set("로그아웃됨")
                self.connect_button.configure(state="normal")
                self.diagnose_button.configure(state="disabled")
                self.logout_button.configure(state="disabled")
                self._write("[SESSION] Session closed.")
            elif event == "error":
                self.status.set("오류 발생")
                if isinstance(payload, Exception):
                    self._write(f"[ERROR] {type(payload).__name__}: {payload}")
                else:
                    self._write("[ERROR] 알 수 없는 오류가 발생했습니다.")
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self.client:
            self.client.close()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
