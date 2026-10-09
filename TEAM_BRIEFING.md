# 팀 개발 브리핑

> 마지막 갱신: 2026-10-08
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
- [x] TCP frame/idle/send 기한과 연결 소유 세션 정리
- [x] LIVE 단계(`connect`/`query`)·원인·시간을 포함하는 호환 진단 결과
- [x] 웹 프로세스 관찰 범위의 유한 label `/metrics`
- [x] DB 없는 발표를 위한 DEMO 전용 점검 경로
- [x] 선택적 Toxiproxy fault runner와 bounded TShark stream 분석

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
| `app/db_probe.py` | LIVE DB 연결·`SELECT 1` 단계와 안전한 오류 분류 |
| `app/diagnostic_models.py` | LIVE 단계·원인·시간 결과의 typed contract |
| `app/metrics.py` | 웹 프로세스의 유한 label 관찰 지표 |
| `app/web_inputs.py` | HTTP body와 요청 값의 bounded validation |
| `app/static/` | 웹 화면 파일 (HTML/CSS/JS) |
| `app/static/guide.html` | DBMS·네트워크·프로그램 기능 학습 안내 페이지 |
| `scripts/` | MariaDB 설치·실행·재시작·검증 도구 |
| `scripts/check_fault_scenarios.py` | 이미 실행 중인 로컬 Toxiproxy에 한정한 선택적 장애 시나리오 |
| `scripts/packet_analysis.py` | TShark stream payload의 제한된 진단 frame 분석 |
| `scripts/packet_tshark.py` | TShark subprocess deadline·output 제한 |
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
- [ ] 발표 시 보여 줄 화면 순서 연습: DEMO 정상 → DEMO 지연 → DEMO 접속 실패 (LIVE는 사전 점검 성공 시 추가)
- [ ] Wireshark로 TCP 9090의 3-way handshake 캡처 연습

보강된 명령은 [기법 적용 기록](docs/TECHNIQUE_ADOPTION.md)에서 확인합니다. DB 없는 발표는 다음 gate를 사용합니다.

```powershell
python -B -m scripts.check_presentation_demo --demo-only --host 127.0.0.1 --demo-port <owned_tcp_port> --web-port <owned_http_port> --require-current-server
```

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
- 발표용 DEMO TCP 9090 캡처: [TCP_3way_handshake_검증.md](TCP_3way_handshake_검증.md)
- 발표용 LIVE MariaDB 3307 캡처: [LIVE_MariaDB_TCP_검증.md](LIVE_MariaDB_TCP_검증.md)
- 2026-10-05 팀원 PC에서 기록한 두 캡처는 `python -B -m scripts.check_packet_evidence`로 재검증합니다. TShark가 필요하며, LIVE 응답은 59번 패킷입니다.
- 개념 학습 페이지: [app/static/guide.html](app/static/guide.html)
- 외부 기법의 출처·라이선스·적용 경계: [docs/TECHNIQUE_ADOPTION.md](docs/TECHNIQUE_ADOPTION.md)
- 발표 슬라이드는 로컬 산출물로 관리하며, 이 저장소에는 프로그램 소스, 협업 문서, 공유용 TCP 캡처를 둡니다. 캡처 공유 전 비밀번호·개인정보 포함 여부를 확인합니다.


## 10. 과제 요구조건과 충족 근거

| 과제 요구조건 | 현재 프로그램의 충족 방식 |
|---|---|
| 통신 시스템 프로그램 | 브라우저·웹 클라이언트·TCP 진단 서버·MariaDB가 요청과 응답을 주고받는 통신 시스템 |
| 클라이언트-서버 구조 | 브라우저는 HTTP 요청, Python 웹 클라이언트는 TCP 요청 중계, TCP 서버는 DBMS 진단을 담당 |
| OSI 7계층 표현 | JSON/UTF-8, 세션 ID, TCP 9090, IPv4, LAN 확장 시 이더넷·Wi-Fi/케이블 역할까지 통신 흐름에 대응 |
| 실생활 응용성 | DB 접속 오류를 네트워크 연결 문제와 DB 로그인·쿼리 문제로 나누어 초기 점검과 담당자 전달을 지원 |

> 단순 채팅이 아니라, 실제 DBMS 접속 상태를 점검하고 응답 시간을 측정하는 진단 목적의 통신 시스템으로 설계했습니다.

## 11. 학교 PC 시연 계획

### 기본 시연: DEMO 모드 권장

학교 PC에서는 **DEMO 모드**를 기본으로 시연합니다. MariaDB를 설치하지 않아도 다음 세 상태를 안정적으로 보여 줄 수 있습니다.

- NORMAL: 정상 통신과 정상 응답
- WARNING: 응답 지연 상황
- DOWN: 접속 실패 상황

### 학교 PC 사전 점검

- [ ] GitHub 저장소 clone 또는 USB로 소스 폴더 복사
- [ ] Python 설치 여부 확인
- [ ] `pip install -r requirements.txt` 실행
- [ ] TCP 서버와 웹 클라이언트 실행
- [ ] 브라우저에서 `http://127.0.0.1:8080` 접속
- [ ] DEMO 정상·지연·접속 실패 3개 상태 확인

### LIVE 모드 주의

LIVE 모드는 실제 MariaDB 설치, PyMySQL, 실행 권한이 필요합니다. 학교 PC의 설치 권한·보안 정책·인터넷 환경에 따라 준비가 막힐 수 있으므로, 발표에서는 **DEMO 모드를 기본 시연**으로 준비하고 LIVE는 가능한 경우에만 추가로 보여 줍니다.

### 발표 시연 추천 순서

1. 프로그램 목적과 클라이언트-서버 구조 소개
2. DEMO 정상 → 응답 지연 → 접속 실패 순서로 결과 화면 시연
3. 요청이 TCP 서버를 거쳐 DBMS 진단 결과로 돌아오는 흐름 설명
4. 해당 흐름이 OSI 7계층 역할과 연결됨을 설명

### Plan B: 실제 MariaDB LIVE 진단 시연

학교 PC 또는 발표 전날 사전 점검 PC에서 MariaDB 설치와 LIVE 모드 실행이 확인되면, DEMO 시연 뒤에 실제 DB 연결 진단을 추가로 보여 줍니다.

#### Plan B 준비 조건

- [ ] Python과 `PyMySQL` 설치
- [ ] 과제용 MariaDB가 로컬 `127.0.0.1:3307`에서 실행 중인지 확인
- [ ] `scripts/install_local_course.ps1`로 해당 PC 전용 MariaDB 초기화
- [ ] `scripts/start_local_live.ps1`로 MariaDB·TCP 서버·웹 클라이언트 실행
- [ ] 브라우저의 LIVE 모드에서 `LIVE · NORMAL` 및 응답 시간 표시 확인

#### Plan B 발표 흐름

1. LIVE 모드에서 MariaDB 로그인과 `SELECT 1` 점검이 성공하는 모습을 보여 줍니다.
2. 화면의 `LIVE · NORMAL`과 응답 시간을 확인합니다.
3. 이 결과가 실제 DBMS 접속·쿼리 과정을 거친 값임을 설명합니다.
4. 문제 발생 시 즉시 DEMO 모드로 전환해 발표 흐름을 유지합니다.

> Plan B는 사전 점검에 성공한 경우에만 사용합니다. 발표 당일 안정성은 DEMO 모드가 보장하므로, LIVE 실패 자체를 발표 현장에서 해결하려고 하지 않습니다.
