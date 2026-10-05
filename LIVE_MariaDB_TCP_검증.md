# 실제 MariaDB 연동 및 TCP 캡처 검증

캡처 파일: `live_mariadb_tcp_capture.pcapng`  
환경: 한 PC의 루프백 `127.0.0.1`, MariaDB Community Server 11.8.9, 진단 서버 9090, DB 포트 3306  
모드·결과: `LIVE · NORMAL`, 읽기 전용 `course_monitor` 계정으로 연결·로그인 및 `SELECT 1` 성공

Wireshark 표시 필터: `tcp.port == 9090 || tcp.port == 3306`

| 구간 | TCP 스트림 | 3-way handshake 패킷 | 확인 내용 |
|---|---:|---|---|
| 진단 클라이언트 → 진단 서버 (`9090`) | 0 | 1 SYN → 2 SYN/ACK → 3 ACK | 세션 생성, 진단 요청, 결과 응답 |
| 진단 서버 → MariaDB (`3306`) | 1 | 10 SYN → 11 SYN/ACK → 12 ACK | MariaDB 인사 메시지, 로그인 및 SQL 통신 |

`9090` 스트림의 **8번 패킷**에는 `diagnose` 요청이, **58번 패킷**에는 `diagnostic_result` 응답이 있습니다. 응답 JSON에 `mode: LIVE`, `status: NORMAL`, `database_connect_ms`, `database_rtt_ms`가 기록되어 있습니다.

`3306` 스트림의 **13번 패킷**에는 MariaDB 11.8.9 서버 인사 메시지가 나타납니다. 이후 연결은 TLS 1.3으로 암호화되어 SQL 문장 자체는 Wireshark에서 평문으로 보이지 않습니다. `SELECT 1` 성공은 프로그램이 받은 DB 응답과 `LIVE · NORMAL` 결과로 확인했습니다. 캡처 파일에는 두 포트의 패킷만 보관했습니다.

루프백 캡처는 물리 케이블·무선 전파와 실제 이더넷 MAC 전달을 증명하지 않습니다. 이 시연의 1·2계층은 가상 루프백 경로의 역할로 설명합니다.
