# 노트북 클라이언트와 LAN 테스트

Windows 노트북을 클라이언트, 기존 데스크톱을 진단 서버로 사용한다. 노트북에는 Python **3.14.3 이상**이 필요하다. 클라이언트는 Python 표준 라이브러리로 실행되며, MariaDB와 PyMySQL은 LIVE를 처리하는 데스크톱에서 준비한다.

이 문서는 실행 준비 절차다. 두 기기 사이의 실제 성공 여부는 아래 실험 기록표에 실행 후 입력한다.

```text
노트북 브라우저
  └─ HTTP 127.0.0.1:8080 → 노트북 Python 웹/TCP 클라이언트
                              └─ LAN TCP 9092 → 데스크톱 진단 서버
                                                    └─ 로컬 MariaDB 3307 (LIVE)
```

## 1. 노트북 준비

1. 제공된 `DBMS-Laptop-Client-날짜-시간.zip`을 노트북으로 복사하고 **전체 압축 해제**한다.
2. PowerShell에서 `python --version`으로 3.14.3 이상인지 확인한다. Python Launcher를 쓰면 `py -3.14 --version`으로 확인한다.
3. 압축을 푼 폴더의 `start-laptop-client.cmd`를 더블클릭한다. 실행 창은 유지한다.
4. 노트북 브라우저에서 `http://127.0.0.1:8080/`을 연다.
5. 화면의 서버 주소에 **데스크톱 LAN IPv4**, 포트에 **9092**를 입력한다. 기본 선택은 LIVE이므로 첫 실험은 DEMO를 직접 선택한다.

8080을 다른 프로그램이 사용하면 폴더에서 PowerShell을 열어 다음 명령을 실행하고 `http://127.0.0.1:8081/`을 연다.

```powershell
.\start-laptop-client.cmd -WebPort 8081
```

## 2. 두 기기의 주소 확인

두 기기를 같은 공유기의 일반 LAN에 연결한다. 유선 연결을 권장하며 Wi-Fi를 쓰면 실제 사용한 매체를 기록한다. 양쪽 PowerShell에서 실행한다.

```powershell
Get-NetIPConfiguration
Get-NetAdapter | Select-Object Name, Status, MacAddress, LinkSpeed
Get-NetConnectionProfile
```

통신에 쓰는 Ethernet/Wi-Fi 인터페이스의 IPv4를 선택한다. 아래 예시의 `192.168.0.10`은 데스크톱, `192.168.0.20`은 노트북 주소이며 **실험 당일 실제 값으로 바꾼다**. WSL·Tailscale 주소는 별도의 가상 경로 실험에 사용한다.

## 3. 데스크톱에서 DEMO 서버 실행

데스크톱의 전체 프로젝트 폴더에서 PowerShell을 열고 실행한다. 이 서버 창은 유지한다.

```powershell
$serverIp = '192.168.0.10' # 데스크톱의 실제 LAN IPv4로 변경
python -B -m app.server --host $serverIp --port 9092
```

프로젝트 가상환경을 사용할 때는 위 `python`을 `.\.venv\Scripts\python.exe`로 바꾼다. LAN 서버의 시작 메시지에서 주소와 9092 포트를 확인한다.

노트북에서는 다음 명령으로 접속 가능 여부를 먼저 확인한다.

```powershell
$serverIp = '192.168.0.10' # 데스크톱과 같은 값
Test-NetConnection -ComputerName $serverIp -Port 9092
```

`TcpTestSucceeded : True`이면 화면의 서버 주소·포트를 맞추고 다음 단계로 간다. 이 포트 확인 명령은 별도 TCP 연결을 만들므로 진단 패킷 캡처 전에 수행한다.

### 연결이 차단될 때

먼저 서버 주소·실행 창·포트를 확인한다. Windows 방화벽 허용이 필요한 경우, 관리 권한과 해당 네트워크의 사용 허가가 있는 환경에서 데스크톱의 관리자 PowerShell로 아래 규칙을 추가한다. **Private 프로필 예시**다. 학교 네트워크의 프로필과 보안 정책은 관리자 안내를 따른다.

```powershell
$serverIp = '192.168.0.10'
$laptopIp = '192.168.0.20'
New-NetFirewallRule -Name 'DBMSCourse-Laptop-9092' `
  -DisplayName 'DBMS course laptop TCP 9092' `
  -Direction Inbound -Action Allow -Protocol TCP `
  -LocalAddress $serverIp -LocalPort 9092 -RemoteAddress $laptopIp -Profile Private
```

규칙 추가 후 노트북에서 포트 확인을 반복한다. 같은 Wi-Fi에서도 게스트망·기기 격리 정책으로 통신이 차단될 수 있다. 주소와 방화벽이 맞으면 공유기 또는 학교 네트워크 정책을 확인한다.

## 4. DEMO 세 상태 확인

노트북에서 다음 순서로 실행한다. 각 상태마다 진단 시작을 누른 뒤 결과 화면을 저장한다.

| 진단 방식 | 모의 상황 | 기대 결과 |
|---|---|---|
| DEMO | 정상 연결 | DEMO / NORMAL |
| DEMO | 응답 지연 경고 | DEMO / WARNING |
| DEMO | DBMS 접속 실패 | DEMO / DOWN |

각 결과의 OSI 3계층에 노트북 출발지 IP와 데스크톱 목적지 IP가 나타나는지 확인한다. TCP 통신은 두 기기 사이에서 수행되며 DB 상태는 선택한 시나리오로 생성된다. 화면에 표시되는 클라이언트 왕복 시간은 실제 요청·처리에 걸린 시간이며, 지연 시나리오를 선택해도 실제 지연 주입 실험으로 기록하지 않는다.

## 5. LIVE까지 확인할 때

데스크톱에서 과제용 MariaDB의 로컬 LIVE 정상 상태를 먼저 확인한다. 기존 프로젝트의 `scripts/start_local_live.ps1` 또는 준비된 로컬 실행 스크립트를 사용한다.

3단계 DEMO 전용 LAN 서버 창에서 Ctrl+C로 그 서버를 종료한다. 같은 프로젝트의 PowerShell에서 아래를 실행하면 DB 설정을 전달한 LAN 서버가 9092에서 동작한다. Python 버전과 DB 설치 경로를 먼저 확인한다.

```powershell
$serverIp = '192.168.0.10' # 실제 데스크톱 LAN IPv4
$courseDbRoot = Join-Path $env:LOCALAPPDATA 'DBMSCourseMariaDB-3307'
$env:PYTHONPATH = Join-Path $courseDbRoot 'pylibs'
$env:DB_HOST = '127.0.0.1'
$env:DB_PORT = '3307'
$env:DB_USER = 'course_monitor'
$env:DB_NAME = 'course_network_demo'
$env:DB_PASSWORD = Get-Content -LiteralPath (Join-Path $courseDbRoot 'secrets\monitor-password.txt') -Raw
try {
    python -B -m app.server --host $serverIp --port 9092
}
finally { Remove-Item Env:DB_PASSWORD -ErrorAction SilentlyContinue }
```

노트북에서 같은 서버 주소·9092를 유지하고 LIVE → 진단 시작을 누른다. `LIVE / NORMAL`과 로그인·SELECT 1 성공 설명을 확인한다. 이 LIVE 지원 서버에서 DEMO 세 상태도 실행할 수 있다. WARNING이 나오면 상세 원인과 연결·쿼리 시간을 기록한다. 기본 500ms 기준 초과도 WARNING 원인이 된다.

## 6. Wireshark 캡처

Wireshark와 Npcap이 준비된 데스크톱 또는 노트북 한 대에서 수행한다.

1. 실제 통신에 쓰는 **Ethernet/Wi-Fi 인터페이스**를 선택한다.
2. 캡처 필터 `tcp port 9092`를 적용하고 **진단 시작 버튼을 누르기 전에** 캡처를 시작한다. ARP도 필요하면 `arp or tcp port 9092`를 사용한다.
3. 노트북에서 DEMO 정상·경고·실패를 각각 한 번 실행한다. LIVE 서버를 준비했다면 LIVE도 한 번 실행한다.
4. 캡처를 정지하고 `lan_laptop_9092_YYYYMMDD.pcapng`로 저장한다.
5. 표시 필터 `tcp.port == 9092`로 좁힌다. 진단 메시지를 포함한 연결의 `tcp.stream` 번호를 확인하고 `tcp.stream == N`으로 해당 연결을 선택한다.
6. 같은 스트림의 SYN → SYN/ACK → ACK, 양쪽 IP·포트·상대 Seq/Ack를 기록한다. 데이터 없는 일반적인 시작은 `Seq=0`, `Seq=0/Ack=1`, `Seq=1/Ack=1`이다.
7. 링크 헤더의 출발지·목적지 MAC을 읽고 인터페이스 기록과 비교한다. MAC은 현재 링크의 전달 대상을 나타낸다.
8. Follow → TCP Stream에서 hello·diagnose·diagnostic_result·logout을 확인한다. 데이터 앞의 4바이트는 응용 메시지 길이를 나타낸다.

전체 프로젝트와 TShark가 있는 데스크톱에서 새 캡처를 재검증할 수 있다. 파일 경로는 실제 저장 경로로 변경한다.

```powershell
python -B -m scripts.check_packet_evidence --capture 'lan_laptop_9092_YYYYMMDD.pcapng' --diagnostic-port 9092
```

1계층 근거로 연결 환경 사진과 인터페이스 링크 상태·속도를 저장한다. 2계층 근거는 캡처의 실제 링크 헤더와 MAC이다. Wi-Fi 호스트 캡처에 Ethernet 헤더가 표시되면 관찰된 헤더 형식을 그대로 기록한다. WSL/VM에서 별도 실험했다면 가상 LAN으로 표기한다.

## 7. 실험 기록과 종료

| 항목 | 실행 후 기록 |
|---|---|
| 날짜·참여자·두 기기의 Python 버전 | |
| 데스크톱·노트북 IP | |
| 사용 매체·인터페이스·링크 속도·MAC | |
| 캡처 파일·인터페이스·링크 헤더 종류 | |
| 서버 9092·클라이언트 임시 포트·TCP stream | |
| SYN/SYN-ACK/ACK 패킷 번호·Seq/Ack | |
| DEMO NORMAL/WARNING/DOWN 결과 | |
| LIVE 결과·연결 및 쿼리 시간 | |
| 결과 화면 파일·구성 및 연결 사진 | |

완료 후 노트북 웹 클라이언트 창과 데스크톱 LAN 서버 창에서 각각 Ctrl+C를 누른다. 이번 실험에서 방화벽 규칙을 추가한 경우 데스크톱 관리자 PowerShell에서 제거한다.

```powershell
Remove-NetFirewallRule -Name 'DBMSCourse-Laptop-9092'
```

| 증상 | 확인 항목 |
|---|---|
| Python 버전 오류 | 3.14.3 이상 설치·PATH, `python --version` 또는 `py -3.14 --version` |
| 노트북 웹 화면이 안 열림 | 실행 창 유지, 8080 충돌 시 WebPort 8081 사용 |
| 웹 화면은 열리지만 진단 통신 오류 | 화면의 데스크톱 LAN 주소·9092, 서버 창, 포트 도달 여부 |
| LIVE 설정 WARNING | 데스크톱 LAN 서버 프로세스의 DB 환경변수·PyMySQL |
| 캡처에 패킷이 없음 | 실제 인터페이스·9092 필터·캡처 시작 순서 |
| handshake는 있으나 JSON 없음 | Test-NetConnection 스트림과 실제 진단 스트림 구분 |

발표 설명 예시 — “노트북의 Python 클라이언트가 LAN을 통해 데스크톱 TCP 9092에 요청합니다. Wireshark에서 양쪽 IP와 TCP 연결 수립을 확인했고, 진단 응답을 노트북 브라우저에 표시했습니다.” 실행 후 확인한 결과에 맞춰 사용한다.
