# 팀 개발 브리핑

> 마지막 갱신: 2026-09-30  
> 저장소: `main` 브랜치 / 비공개 협업 저장소

## 1. 프로젝트 한 줄 소개

**DBMS 네트워크 접속 진단 통신 시스템**은 브라우저에서 진단을 요청하면 Python 진단 서버가 MariaDB 연결 상태와 응답 시간을 확인하고, 결과를 다시 화면에 표시하는 클라이언트-서버 기반 통신 프로그램입니다.

- 목적: DB 접속 오류가 발생했을 때 **네트워크 연결 문제인지, DB 로그인·쿼리 문제인지** 빠르게 구분하도록 지원
- 발표 핵심: 클라이언트-서버 구조와 OSI 7계층 적용을 실제 통신 흐름으로 보여 줌
- 현재 방식: 같은 PC의 루프백(`127.0.0.1`) 환경에서 시연. LAN 환경으로 확장 가능

## 2. 현재 구현 완료 현황

- [x] 브라우저 웹 클라이언트 (HTTP 8080)
- [x] TCP 진단 서버 (TCP 9090)
- [x] JSON/UTF-8 기반 요청·응답 및 애플리케이션 세션 ID
- [x] DEMO 모드: 정상 / 응답 지연 / 접속 실패 시연
- [x] LIVE 모드: MariaDB 로그인 및 `SELECT 1` 점검
- [x] 응답 시간 측정과 NORMAL / WARNING / DOWN 상태 구분
- [x] OSI 7계층 적용 정보 표시
- [x] 단위·통합 테스트 (`tests/`)
- [x] 로컬 MariaDB 설치·실행·점검 스크립트
- [x] 학습용 안내 페이지 (`app/static/guide.html`)

## 3. 프로그램 구조와 요청 흐름

`브라우저 → Python 웹 클라이언트 → TCP 진단 서버 → MariaDB`

1. 사용자가 브라우저에서 진단 방식을 선택하고 **DBMS 진단 시작**을 누릅니다.
2. 웹 클라이언트(`app/web_client.py`)가 HTTP 요청을 받아 TCP 진단 클라이언트를 실행합니다.
3. TCP 클라이언트(`app/client_core.py`)가 JSON 메시지로 서버(`app/server.py`, 포트 9090)에 세션 생성·진단·종료 요청을 보냅니다.
4. 서버가 `app/diagnostics.py`를 통해 MariaDB에 연결하고 `SELECT 1`을 수행합니다.
5. 결과·원인·응답 시간을 JSON으로 반환하고, 브라우저가 화면에 표시합니다.

## 4. 주요 폴더 안내

| 경로 | 역할 |
|---|---|
| `app/web_client.py` | HTTP 8080 웹 클라이언트 및 API |
| `app/server.py` | TCP 9090 진단 서버, 세션·요청 검증 |
| `app/client_core.py` | JSON-over-TCP 클라이언트 |
| `app/protocol.py` | JSON·UTF-8 및 메시지 길이 프레이밍 |
| `app/diagnostics.py` | LIVE/DEMO 진단 로직, 응답 시간 측정 |
| `app/static/` | 웹 화면 파일 (HTML/CSS/JS) |
| `app/static/guide.html` | DBMS·네트워크·프로그램 기능 학습 안내 페이지 |
| `scripts/` | MariaDB 설치·실행·재시작·검증 도구 |
| `tests/` | 프로토콜·진단·웹 클라이언트 테스트 |

## 5. OSI 7계층과 프로그램의 연결

| 계층 | 프로그램에서 확인할 수 있는 역할 |
|---|---|
| 7 응용 | 브라우저 진단 요청, DBMS 진단 결과 표시 |
| 6 표현 | JSON 직렬화, UTF-8 인코딩 |
| 5 세션 | 서버가 발급·검증하는 세션 ID |
| 4 전송 | TCP socket, 포트 9090 |
| 3 네트워크 | IPv4 주소(현재 `127.0.0.1`) |
| 2 데이터링크 | LAN 확장 시 이더넷/Wi-Fi 인터페이스 |
| 1 물리 | LAN 확장 시 케이블 또는 무선 전송 매체 |

> 현재 시연은 루프백 환경이므로 1·2계층은 LAN 확장 시 적용되는 역할로 설명합니다.

## 6. 팀원이 바로 해야 할 일

### 환경 준비

1. GitHub 초대를 수락하고 저장소를 clone합니다.
2. Python 환경을 준비한 뒤 `pip install -r requirements.txt`를 실행합니다.
3. 기본 시연은 DEMO 모드로 가능하며 DB 설치 없이도 정상·지연·실패 상태를 확인할 수 있습니다.
4. LIVE 모드는 MariaDB가 필요합니다. 본인 PC에서만 `scripts/install_local_course.ps1`를 실행해 과제용 DB를 설치합니다. 기존 DB나 동아리 프로젝트 DB는 건드리지 않습니다.

### 우선 확인할 TODO

- [ ] 본인 PC에서 DEMO 모드 실행 및 세 상태 확인
- [ ] 본인 PC에서 LIVE 모드 실행 및 MariaDB 연결 확인
- [ ] `python -m unittest discover -s tests -v` 테스트 실행
- [ ] 발표 시 보여 줄 화면 순서 결정: LIVE 정상 → DEMO 지연 → DEMO 접속 실패
- [ ] Wireshark로 TCP 9090의 3-way handshake 캡처 연습

## 7. 협업 규칙

- 작업 시작 전: `git pull`
- 기능 수정은 가능한 한 **한 기능 단위**로 커밋합니다.
- 같은 파일을 동시에 크게 수정해야 하면 먼저 카카오톡으로 역할을 나눕니다.
- 커밋 전: DEMO 실행 또는 테스트로 기본 동작을 확인합니다.
- **절대 업로드 금지:** DB 비밀번호, `secrets/`, MariaDB 데이터 폴더, 개인 PC 로그, 발표용 개인정보

## 8. 다음 개발 우선순위

1. 팀원 환경에서 재현 가능한지 확인
2. LIVE 모드 오류 메시지와 화면 표현을 실제 시연 기준으로 다듬기
3. Wireshark 캡처 자료를 발표·최종 보고서 근거로 정리
4. 필요 시 LAN의 다른 PC에서도 접속할 수 있도록 주소·방화벽 설정을 별도 검토

## 9. 참고

- 전체 실행·설치 절차: [README.md](README.md)
- 개념 학습 페이지: [app/static/guide.html](app/static/guide.html)
- 발표 자료는 로컬 산출물로 관리하며, 이 저장소에는 프로그램 소스와 협업 문서만 둡니다.
