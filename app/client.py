"""Tkinter GUI for the DBMS diagnostic communication system."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable

from .client_core import DiagnosticClient


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DBMS 네트워크 접속 진단 통신 시스템")
        self.geometry("1030x720")
        self.minsize(900, 620)
        self.client: DiagnosticClient | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
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

    def _run(self, action: Callable[[], object], event: str) -> None:
        def worker() -> None:
            try:
                self.events.put((event, action()))
            except Exception as exc:
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
                response = payload  # type: ignore[assignment]
                self.status.set("연결됨 · 세션이 생성되었습니다")
                self.connect_button.configure(state="disabled")
                self.diagnose_button.configure(state="normal")
                self.logout_button.configure(state="normal")
                self._write(f"[SESSION]\n{response['message']}\n세션 ID: {response['session_id'][:12]}…")  # type: ignore[index]
            elif event == "diagnosed":
                response = payload  # type: ignore[assignment]
                if response.get("type") == "error":  # type: ignore[union-attr]
                    self._write(f"[ERROR] {response.get('message')}")  # type: ignore[union-attr]
                else:
                    result = response["result"]  # type: ignore[index]
                    lines = [
                        "[DBMS DIAGNOSTIC]",
                        f"상태: {result['status']}  |  모드: {result['mode']}",
                        f"대상: {result['database']}",
                        f"설명: {result['detail']}",
                        f"클라이언트 왕복 시간: {response['client_rtt_ms']} ms",  # type: ignore[index]
                        "",
                        "[OSI 7-LAYER TRACE]",
                    ]
                    lines.extend(f"{item['layer']}: {item['application']}" for item in response["osi_trace"])  # type: ignore[index]
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
                self._write(f"[ERROR] {type(payload).__name__}: {payload}")
        self.after(100, self._drain_events)

    def _on_close(self) -> None:
        if self.client:
            self.client.close()
        self.destroy()


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
