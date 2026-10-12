# DBMS 네트워크 접속 진단 통신 시스템

초고속 네트워크 2 과제용 독립 프로그램입니다. 클라이언트가 TCP로 서버에 DBMS 접속 진단을 요청하면, 서버가 연결 상태와 응답 시간을 JSON으로 반환합니다. 클라이언트는 진단 결과와 OSI 7계층별 적용 정보를 표시합니다.

## 과제 개발 기준

사용자가 전달한 교수님 설명에 따라, 이 프로젝트의 우선 기준은 **통신 시스템, 명확한 클라이언트–서버 구조, OSI 7계층 표현, 내 PC에서 직접 실행한 요청·응답 데이터**입니다. 기능의 깊이나 화려함보다 통신 흐름과 근거를 명확하게 보여 주는 것을 우선합니다.

핵심 시연은 Python TCP 클라이언트(`app/client_core.py`)가 직접 만든 TCP 서버(`app/server.py`)에 진단을 요청하고, 서버가 MariaDB 연결·로그인·`SELECT 1` 결과를 반환하는 과정입니다. 브라우저는 HTTP 화면이며, Python 웹 클라이언트가 TCP 통신을 담당합니다. DB 접속 장애의 네트워크·로그인·쿼리 원인 구분과 응답 시간 측정을 실생활 응용 포인트로 설명합니다.

OSI 5·6계층은 응용 프로그램의 세션·데이터 표현 기능에 대응합니다. 같은 PC의 루프백에서는 1·2계층의 실제 케이블·Wi-Fi·이더넷 프레임 전송을 주장하지 않습니다. Toxiproxy와 상세 metrics는 선택적인 추가 검증 도구입니다.

교수님이 추가로 강조한 **OSI 1~4계층에 발표·보고서의 비중을 둡니다.** 3계층의 출발지·목적지 IP와 4계층의 포트, TCP 연결 수립·Sequence/Acknowledgment 번호를 실제 캡처로 설명합니다. 1·2계층은 루프백의 한계를 먼저 밝히고, 실제 매체·링크 상태와 프레임/MAC 전달을 보여 주려면 서로 다른 PC의 LAN 통신을 추가로 관찰해야 합니다. 계층별 현재 근거와 추가 확인 항목은 팀 브리핑에 구분했습니다.

제출 참고 양식은 **개인 프로그램 개발 보고서**이며 요약, 프로그램 소개, OSI 기준 구성·작동, 실제 동작 과정, 장점·한계 순서로 준비합니다. 요구조건별 근거와 양식에 맞춘 작성 체크리스트는 [팀 개발 브리핑](TEAM_BRIEFING.md)에 정리했습니다.

## 구성

```text
Tkinter Client ── TCP / JSON ── Python Server ── PyMySQL (선택) ── MariaDB
                                      └───────── 모의 진단 모드
```

- `app/server.py`: TCP 서버. 세션, 요청 검증, DBMS 진단을 처리합니다.
- `app/client.py`: Tkinter GUI 클라이언트입니다. 시스템 Python에 Tcl/Tk가 설치돼 있을 때 사용합니다.
- `app/web_client.py`: 브라우저에서 실행하는 로컬 웹 클라이언트입니다. 별도 GUI 라이브러리 설치가 필요 없습니다.
- `app/demo_client.py`: GUI 없이 통신을 확인하는 콘솔 클라이언트입니다.
- `tests/test_protocol.py`: 메시지 프레이밍과 클라이언트-서버 왕복 테스트입니다.
- `tests/test_web_client.py`: 브라우저 HTTP 요청부터 TCP 진단 응답까지 확인합니다.

## 실행

Python 3.9 이상을 PATH에 추가한 뒤 PowerShell 창 두 개를 열어 아래 순서로 실행합니다.

```powershell
# 1. 서버
python -m app.server

# 2. 브라우저 클라이언트
python -m app.web_client
```

브라우저에서 `http://127.0.0.1:8080`을 연 뒤 진단 방식을 선택하고 **DBMS 진단 시작**을 누릅니다. 진단 요청마다 웹 클라이언트가 TCP 세션을 생성해 진단 서버에 요청하고, 결과를 화면에 표시합니다.

`LIVE`는 실제 DBMS에 연결합니다. DB 설정이 없으면 경고를 반환하며, 모의 결과로 대체하지 않습니다. `DEMO`는 DB 연결 없이 정상·지연·접속 실패 상황을 시연합니다. 화면과 응답에 DEMO라고 표시되므로 실제 장애로 해석하면 안 됩니다.

화면의 `통신 경로`는 브라우저 화면 → 웹 클라이언트 → TCP 진단 서버 → MariaDB(또는 DEMO 모의 상태)를 보여 줍니다. TCP 클라이언트는 브라우저 자체가 아니라 웹 클라이언트의 Python 코드이며, 브라우저는 HTTP로 그 결과를 표시합니다.

## 실제 MariaDB 연동

과제 전용 MariaDB 11.8.9는 `%LOCALAPPDATA%\DBMSCourseMariaDB-3307`에 설치하도록 구성했습니다. 기존 과제 DB 프로세스가 3306 포트를 사용하고 파일은 사용자 계정에서 보이지 않아, 새 설치를 3307 포트와 별도 폴더로 분리했습니다. Windows 서비스로 등록하지 않고 `127.0.0.1:3307`에서만 실행합니다. 데이터베이스는 `course_network_demo`, 프로그램 계정은 읽기 전용 `course_monitor`입니다. 암호는 저장소 밖의 `secrets` 폴더에 보관하며 공개하거나 제출하지 마세요.

처음 설치할 때는 **본인 계정의 PowerShell**에서 과제 폴더로 이동해 다음을 실행합니다. MariaDB 공식 Windows ZIP과 PyMySQL을 내려받고 과제 전용 DB를 초기화합니다. 기존 3306 DBMS나 동아리 프로젝트는 변경하지 않습니다. 다운로드한 ZIP의 SHA-256을 확인하며, 3307 포트 또는 같은 이름의 설치 폴더가 이미 있으면 설치하지 않고 중단합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\scripts\install_local_course.ps1"
```

과제용 서버가 실행되지 않을 때는 이 프로젝트 폴더에서 다음을 실행합니다. 재부팅 자체는 필수가 아닙니다.

```powershell
& .\scripts\start_local_live.ps1
```

그다음 `http://127.0.0.1:8080/`에서 `LIVE · 실제 MariaDB 진단`을 선택하고 **DBMS 진단 시작**을 누르면 실제 연결 성공 시 `LIVE · NORMAL`이 표시됩니다. 발표 시 세 상태를 보여 주려면 `DEMO · 모의 상황 시연`으로 전환한 뒤 모의 상황을 선택합니다.

현재처럼 구버전 TCP 서버(9090)가 종료되지 않은 세션에서는 웹 화면이 DEMO 요청을 임시 서버(9091)로 자동 연결할 수 있습니다. 화면에 `DEMO`와 `9091`이 명시됩니다. 서버가 새 코드로 다시 실행된 뒤에는 9090 하나로 두 모드를 처리합니다.

재부팅 없이 과제 프로세스만 새 코드로 교체하려면, 본인 Windows PowerShell에서 이 프로젝트 폴더로 이동한 뒤 다음을 실행합니다. 스크립트는 8080·9090·9091의 실행 파일·모듈·포트를 모두 확인하고, **일치하는 과제 Python 프로세스만** 종료합니다. 다른 프로세스가 포트를 사용하면 아무것도 종료하지 않고 중단합니다. MariaDB는 종료하지 않습니다.

```powershell
& .\scripts\restart_local_course.ps1
```

시작 스크립트는 새 TCP 서버의 버전과 LIVE·DEMO 결과를 확인하므로, 구버전 서버를 새 버전으로 잘못 판단하지 않습니다.

다른 실제 DBMS를 사용하려면 `PyMySQL`을 설치하고 아래 환경 변수를 설정한 뒤 서버를 실행합니다.

```powershell
python -m pip install PyMySQL
$env:DB_HOST='127.0.0.1'
$env:DB_PORT='3306'
$env:DB_USER='사용자명'
$env:DB_PASSWORD='비밀번호'
$env:DB_NAME='데이터베이스명'
```

웹 화면의 LIVE 요청에서 `DB_HOST`가 없으면 `LIVE · WARNING`을 반환합니다. `DB_HOST`가 설정되면 PyMySQL로 DB에 연결·로그인하고 `SELECT 1`을 실행합니다. 네트워크 연결 실패는 `LIVE · DOWN`, 로그인·쿼리·의존성에 문제가 있으면 `LIVE · WARNING`, 전체 과정이 성공하면 `LIVE · NORMAL`입니다. 전체 진단 시간이 500ms를 넘는 경우에도 `WARNING`으로 표시합니다. `DEMO`는 실제 DB 연결을 증명하지 않습니다.

## OSI 7계층 적용

| 계층 | 이 프로그램의 적용 |
|---|---|
| 7 응용 | 연결, DBMS 진단, 로그아웃 요청 |
| 6 표현 | JSON 직렬화와 UTF-8 인코딩 |
| 5 세션 | 서버가 발급하는 세션 ID와 요청 검증 |
| 4 전송 | TCP socket, 포트 9090; 4바이트 길이 헤더는 TCP 기능이 아닌 응용 메시지 구분 방식 |
| 3 네트워크 | IPv4 출발지·목적지 주소 기록 |
| 2 데이터링크 | 네트워크 인터페이스 MAC 주소 표시 |
| 1 물리 | 루프백 또는 유선·무선 전송 매체 표시 |

한 PC의 루프백 시연에서 1·2계층은 설명용 정보입니다. 실제 이더넷 프레임이나 Wi-Fi 전파가 루프백을 지나간다는 뜻은 아닙니다.
5·6계층 역시 별도 OSI 표준 프로토콜을 구현했다는 뜻이 아니라, 이 프로그램의 세션 관리와 데이터 표현 기능을 해당 계층의 역할에 대응시킨 것입니다.

## Wireshark 검증

발표용 DEMO 캡처는 [demo_tcp_9090_20261005.pcapng](demo_tcp_9090_20261005.pcapng), 패킷별 판독 결과는 [TCP_3way_handshake_검증.md](TCP_3way_handshake_검증.md)에 있습니다. Wireshark에서 `tcp.port == 9090` 필터를 적용하면 3-way handshake(1~3번), 세션 생성, 진단 요청·응답(10번), 로그아웃을 확인할 수 있습니다. 총 19개 패킷이며 진단 결과는 `DEMO · NORMAL`입니다.

발표용 LIVE 캡처는 [live_mariadb_3307_20261005.pcapng](live_mariadb_3307_20261005.pcapng), 판독 결과는 [LIVE_MariaDB_TCP_검증.md](LIVE_MariaDB_TCP_검증.md)에 있습니다. 2026-10-05 팀원 PC의 MariaDB **3307** 환경에서 기록한 68개 패킷으로, 표시 필터는 `tcp.port == 9090 || tcp.port == 3307`입니다. LIVE 정상 진단 응답은 **59번** 패킷입니다. 이전 3306 캡처를 대체하며, 기존 자료는 Git 이력에 남아 있습니다.

3307 통신이 MySQL/MariaDB로 표시되지 않으면 Wireshark의 **Analyze → Decode As…**에서 TCP 포트 3307을 `MySQL`로 지정합니다. 아래 검증 스크립트는 TShark의 `-d tcp.port==3307,mysql` 옵션을 자동으로 적용합니다. TLS로 암호화된 로그인·SQL 내용을 직접 읽을 수는 없으며, `SELECT 1` 성공은 프로그램의 LIVE 정상 응답으로 확인합니다.

Wireshark에 포함된 TShark가 설치되어 있으면 두 캡처의 3-way handshake, MariaDB 인사·TLS, LIVE/DEMO 응답을 다음 명령으로 재검증할 수 있습니다. 캡처 파일은 수정하지 않습니다.

```powershell
python -B -m scripts.check_packet_evidence
```

## 테스트

```powershell
python -m unittest discover -s tests -v
```

발표 직전에는 실행 중인 웹 화면·LIVE·DEMO 3상태·OSI 7계층을 한 번에 점검할 수 있습니다. 현재 임시 서버(9091)를 사용할 때:

```powershell
python -B -m scripts.check_presentation_demo --demo-port 9091
```

나중에 새 TCP 서버가 9090 하나로 실행되면 `--demo-port 9091`을 빼고 같은 명령을 사용합니다.

## 외부 기법 적용과 추가 검증

이번 보강은 기존 동기 TCP/JSON 구조를 유지하면서 프레임 수신 기한, 연결 소유 세션, 단계별 LIVE 진단, 유한 label metrics, 선택적 장애 주입, 스트림 기반 캡처 판독을 추가합니다. 적용 근거와 출처별 라이선스·로컬 파일·검증 범위는 [기법 적용 기록](docs/TECHNIQUE_ADOPTION.md)에 정리했습니다.

프로토콜은 **v2, 4바이트 big-endian 길이 헤더, 최대 1 MiB payload**를 그대로 사용합니다. Real Python 예제의 버퍼 누적 원리만 참고하며, 그 예제의 JSON header/2바이트 길이 형식을 wire contract로 가져오지 않습니다. PyShark·Prometheus/Grafana·Toxiproxy는 기본 실행에 필요한 의존성이 아니며 TShark와 Toxiproxy 검증은 설치돼 있고 명시적으로 선택한 경우에만 실행합니다.

### 시간 제한과 LIVE 결과

- TCP idle은 60초, 전체 frame 수신은 10초, send는 5초입니다.
- `DiagnosticClient` timeout을 생략하면 `DIAGNOSTIC_TIMEOUT_SECONDS`를 사용하고 기본값은 10초입니다. 값은 유한한 양수이고 120초 이하이며, 생성자에 지정한 timeout이 환경값보다 우선합니다.
- DB connect/read/write timeout은 `DB_CONNECT_TIMEOUT_SECONDS`, `DB_READ_TIMEOUT_SECONDS`, `DB_WRITE_TIMEOUT_SECONDS`가 각각 기본 3초이며 0보다 큰 유한한 값, 최대 30초인 **연산별 제한**입니다. 기본값이 전체 진단을 10초 이내로 보장하는 것은 아니므로 큰 DB 예산에는 충분히 큰 client timeout을 지정합니다.
- `DB_WARNING_MS`는 기본 500ms인 0보다 큰 유한한 값입니다. `query_ms`, `total_ms`, `failed_phase`, `error_kind`, `db_errno`는 기존 응답 key를 깨지 않는 선택적 LIVE 필드입니다.

LIVE의 connect 시간은 PyMySQL 연결·인증 호출 전체이며 TCP/TLS/auth를 독립 측정했다는 뜻이 아닙니다. `SELECT 1`은 query 단계로 별도 측정합니다. 유효한 LIVE `WARNING`/`DOWN` 진단은 HTTP 200으로 전달하고, TCP timeout·reset처럼 진단을 받지 못한 경우에만 HTTP 504/502를 사용합니다. client timeout이 먼저 만료되면 socket/session을 정리하고 DEMO로 대체하지 않습니다.

### DB 없는 발표 점검

MariaDB가 없는 PC에서는 자체 서비스의 소유 port를 넣어 DEMO만 확인합니다.

```powershell
python -B -m scripts.check_presentation_demo --demo-only --host 127.0.0.1 --demo-port <owned_tcp_port> --web-port <owned_http_port> --require-current-server
```

이 gate는 DEMO의 NORMAL·WARNING·DOWN과 현재 서버 식별을 확인하며 LIVE 성공을 주장하지 않습니다. LIVE full gate와 `scripts/check_live.py`는 실제 DB 설정과 실행 중인 LIVE 서버가 준비된 경우에만 실행합니다. `check_live.py`에는 별도 `--help` 옵션이 없습니다.

### Metrics 범위

웹 프로세스의 `GET /metrics`는 해당 프로세스가 관찰한 요청만 집계하며 scrape 자체가 DB에 접속하지 않습니다. `mode`, `outcome`, `status`, `error_kind`는 정해진 유한 집합이고 세션 ID·DB host/user/name·원문 예외는 label이 되지 않습니다. DEMO 관측은 LIVE 상태를 갱신하지 않으며 process 재시작 시 metrics가 초기화됩니다. 직접 TCP와 Tkinter 요청은 웹 metrics 범위 밖입니다.

### 선택적 장애·패킷 검증

Toxiproxy는 이미 실행 중인 로컬 API에서 지정한 upstream/listen port에 유일한 proxy를 만들 때만 사용합니다. 기존 proxy와 전역 설정을 건드리지 않고, 자신이 만든 toxic/proxy를 모든 종료 경로에서 정리합니다.

```powershell
python -B -m scripts.check_fault_scenarios --toxiproxy-url http://127.0.0.1:8474 --target diagnostic --mode demo --upstream 127.0.0.1:<owned_tcp_port> --listen 127.0.0.1:<free_proxy_port>
```

이 명령은 이미 실행 중인 로컬 Toxiproxy API만 사용하며 설치나 daemon 시작을 하지 않습니다. exit 0은 fault와 recovery 검증 성공, exit 1은 assertion 또는 owned-proxy 계약 실패, exit 2는 API 부재와 같은 prerequisite skip을 뜻합니다.

기존 캡처 gate는 유지하고, 새 stream 분석은 캡처와 port를 명시합니다.

```powershell
python -B -m scripts.check_packet_evidence
python -B -m scripts.check_packet_evidence --capture demo_tcp_9090_20261005.pcapng --diagnostic-port 9090
python -B -m scripts.check_packet_evidence --capture live_mariadb_3307_20261005.pcapng --diagnostic-port 9090 --db-port 3307
```

각 TShark subprocess는 15초의 실행 기한과 stdout/stderr 각각 4 MiB 상한을 가지며, 한 캡처에서 매칭하는 TCP stream은 최대 128개, 전체 decoded stream bytes는 8 MiB로 제한됩니다.
TShark stream 분석은 분할·병합된 4바이트 length-prefixed 메시지, SYN/SYN-ACK/ACK 관계, 재전송·순서 이상·reset 관찰을 애플리케이션 status와 별도로 보고합니다. TLS가 적용된 MariaDB 캡처의 SQL 내용을 읽었다고 표시하지 않으며, loopback 전송 이상을 자동으로 DB DOWN으로 판정하지 않습니다.

전체 적용 근거와 검증 명령의 실제 결과는 `.omo/evidence/upstream-adoption/task6-docs.md`에 기록합니다.

