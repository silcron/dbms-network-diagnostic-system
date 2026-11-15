"""Turn one diagnostic snapshot into operator feedback without probing again."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final, NamedTuple

from .protocol import JsonObject


class Feedback(NamedTuple):
    finding: str
    next_check: str


_NORMAL: Final = Feedback("로그인·쿼리 성공", "추가 조치는 없습니다.")
_SLOW: Final = Feedback("쿼리 성공 · 응답 지연", "다시 진단해 지연이 반복되는지 확인하세요. 반복되면 DB 연결·인증 시간과 SELECT 1 시간을 비교하세요.")
_NETWORK: Final = Feedback("DB 연결 실패", "진단 서버에 설정한 DB 주소·포트와 해당 MariaDB 서비스의 실행 상태를 확인하세요.")
_CAUSES: Final = {
    "dependency": Feedback("DB 드라이버 없음", "진단 서버를 실행한 Python 환경에 PyMySQL을 설치한 뒤 다시 진단하세요."),
    "config": Feedback("DB 접속 설정 오류", "결과에 표시된 설정값을 진단 서버에서 수정한 뒤 다시 진단하세요."),
    "network": _NETWORK,
    "timeout": Feedback("DB 연결 시간 초과", "진단 서버에서 DB에 연결할 수 있는지 확인한 뒤 DB_CONNECT_TIMEOUT_SECONDS 설정을 확인하세요."),
    "query": Feedback("쿼리 결과 확인 실패", "진단에 사용한 계정으로 SELECT 1을 실행해 결과가 1인지 확인하세요."),
    "internal": Feedback("진단 처리 오류", "확인 시각에 해당하는 진단 서버와 MariaDB 로그를 확인하세요."),
}
_QUERY_CAUSES: Final = {
    "network": Feedback("쿼리 중 연결 끊김", "로그인은 성공했습니다. SELECT 1 실행 시각의 MariaDB 로그와 연결 중단 기록을 확인하세요."),
    "timeout": Feedback("SELECT 1 응답 시간 초과", "연결 후 쿼리 응답을 기다리다 중단됐습니다. MariaDB 부하와 DB_READ_TIMEOUT_SECONDS 설정을 확인하세요."),
    "auth": Feedback("쿼리 실행 권한 확인 필요", "진단 계정의 권한과 MariaDB 로그를 확인한 뒤 SELECT 1을 다시 실행하세요."),
    "database": Feedback("쿼리 실행 중 DB 오류", "해당 데이터베이스에서 같은 계정으로 SELECT 1을 실행하고 MariaDB 로그를 확인하세요."),
}
_UNKNOWN: Final = Feedback("세부 원인 미확인", "결과 설명과 진단 시각의 서버 로그를 확인한 뒤 다시 진단하세요.")


def target_feedback(db: JsonObject) -> Feedback:
    """Interpret phase and cause together; a WARNING alone does not prove latency."""
    status = db.get("status")
    if db.get("mode") == "DEMO":
        examples = {"normal": _NORMAL, "warning": _SLOW, "down": _NETWORK}
        example = examples.get(str(db.get("scenario")), _UNKNOWN)
        return Feedback(f"모의 · {example.finding}", f"점검 예시: {example.next_check} 실제 상태는 LIVE로 확인하세요.")

    kind = db.get("error_kind")
    if db.get("db_errno") == 2059 and db.get("failed_phase") == "connect":
        return Feedback("인증 플러그인 오류", "DB 계정의 인증 방식이 진단 서버의 PyMySQL에서 지원되고 설정되어 있는지 확인하세요.")
    if db.get("failed_phase") == "query" and isinstance(kind, str) and kind in _QUERY_CAUSES:
        return _QUERY_CAUSES[kind]
    slot = db.get("target_id")
    prefix = f"DB{slot}_" if type(slot) is int and slot in (2, 3) else "DB_"
    if kind == "auth":
        return Feedback("로그인 거부", f"진단 서버의 {prefix}USER·{prefix}PASSWORD와 해당 계정의 접속 허용 호스트를 확인하세요.")
    if kind == "database":
        return Feedback("대상 DB 접근 실패", f"{prefix}NAME에 지정한 DB가 있는지, 진단 계정으로 접근할 수 있는지 확인하세요.")
    if isinstance(kind, str) and kind in _CAUSES:
        return _CAUSES[kind]
    if status == "NORMAL":
        return _NORMAL
    if status == "WARNING" and kind == "none" and db.get("failed_phase") is None:
        return _SLOW
    return _UNKNOWN


def batch_summary(targets: Sequence[JsonObject]) -> str:
    """Group real slot IDs by severity, keeping equal-priority targets together."""
    if len(targets) < 2:
        return ""
    demo = targets[0].get("mode") == "DEMO"
    groups = {
        status: [f"DB {db['target_id']}" for db in targets if db.get("status") == status]
        for status in ("DOWN", "WARNING", "NORMAL")
    }
    prefix = "모의 결과" if demo else "이번 진단"
    counts = f"정상 {len(groups['NORMAL'])}개 · 경고 {len(groups['WARNING'])}개 · 장애 {len(groups['DOWN'])}개"
    steps: list[str] = []
    for status in ("DOWN", "WARNING"):
        if groups[status]:
            label = "다음 점검" if steps else "우선 점검"
            steps.append(f"{label}: {', '.join(groups[status])}")
    priority = " → ".join(steps) if steps else "모든 대상이 정상입니다."
    return f"{prefix} · {counts}\n{priority}"
