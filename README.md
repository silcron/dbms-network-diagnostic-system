# DBMS 네트워크 접속 진단 통신 시스템

초고속 네트워크 2 과제용 독립 프로그램입니다. 클라이언트가 TCP로 서버에 DBMS 접속 진단을 요청하면, 서버가 연결 상태와 응답 시간을 JSON으로 반환합니다. 클라이언트는 진단 결과와 OSI 7계층별 적용 정보를 표시합니다.

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

실제 TCP 캡처와 패킷별 판독 결과는 `tcp_9090_loopback_capture.pcapng` 및 `TCP_3way_handshake_검증.md`에 있습니다. Wireshark에서 캡처 파일을 열고 `tcp.port == 9090` 필터를 적용하면 3-way handshake, 세션 생성, 진단 요청·응답, 로그아웃을 확인할 수 있습니다.

기존 MariaDB 연동 캡처는 `live_mariadb_tcp_capture.pcapng`와 `LIVE_MariaDB_TCP_검증.md`에 있습니다. 이 캡처는 이전 3306 실행에서 기록한 것이므로 표시 필터는 `tcp.port == 9090 || tcp.port == 3306`입니다. 새 3307 설치의 패킷 캡처와 혼동하지 마세요.

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

