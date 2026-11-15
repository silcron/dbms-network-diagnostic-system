> 이전 설계·실험 기록입니다. 현재 DB 3개 확장본은 [README](README.md), 최신 증빙은 [최종 실행 자료](evidence/final-20261114/)를 참고하세요.

# 실제 MariaDB 연동 및 TCP 캡처 검증

캡처 파일: [live_mariadb_3307_20261005.pcapng](live_mariadb_3307_20261005.pcapng)  
캡처 일자: 2026-10-05 (Asia/Seoul), 총 68개 패킷  
환경: 한 PC의 루프백 `127.0.0.1`, MariaDB Community Server 11.8.9, 진단 서버 9090, DB 포트 3307  
모드·결과: `LIVE · NORMAL`. 로컬 실행 설정의 읽기 전용 `course_monitor` 계정으로 진단했으며, 연결·로그인 및 `SELECT 1` 성공은 프로그램 응답으로 확인했습니다.

Wireshark 표시 필터: `tcp.port == 9090 || tcp.port == 3307`

3307 통신이 MySQL/MariaDB로 자동 인식되지 않으면 **Analyze → Decode As…**에서 TCP 포트 3307을 `MySQL`로 지정합니다. TShark에서는 `-d tcp.port==3307,mysql`을 사용합니다.

| 구간 | TCP 스트림 | 3-way handshake 패킷 | 확인 내용 |
|---|---:|---|---|
| 진단 클라이언트 → 진단 서버 (`9090`) | 0 | 1 SYN → 2 SYN/ACK → 3 ACK | 세션 생성, 진단 요청, 결과 응답 |
| 진단 서버 → MariaDB (`3307`) | 1 | 10 SYN → 11 SYN/ACK → 12 ACK | MariaDB 인사 메시지, TLS 협상 및 암호화된 데이터 |

`9090` 스트림의 **8번 패킷**에는 `diagnose` 요청이, **59번 패킷**에는 `diagnostic_result` 응답이 있습니다. 응답 JSON에 `mode: LIVE`, `status: NORMAL`, `database_connect_ms`, `database_rtt_ms: 36.17`과 OSI 7계층 기록이 있습니다. 36.17ms는 이 캡처의 측정값이며 재실행 시 달라질 수 있습니다.

`3307` 스트림의 **13번 패킷**에는 MariaDB 11.8.9 서버 인사 메시지가 나타납니다. **19번과 23번 패킷**의 서버 TLS handshake에서 선택한 버전 `0x0304`로 TLS 1.3 협상을 확인할 수 있습니다. 이후 연결은 암호화되어 로그인 정보와 SQL 문장 자체는 Wireshark에서 평문으로 보이지 않습니다. `SELECT 1` 성공은 프로그램의 `LIVE · NORMAL` 결과로 확인했으며, SQL 문장을 복호화해 확인했다는 뜻은 아닙니다. 캡처 파일에는 두 포트의 루프백 패킷만 보관했습니다.

저장소 루트에서 `python -B -m scripts.check_packet_evidence`를 실행하면 두 TCP 연결의 handshake, MariaDB 인사, TLS 1.3 협상, LIVE 정상 응답을 재검증합니다. 이 파일은 이전 3306 환경 캡처를 대체하며, 이전 자료와 응답 패킷 번호(58번)를 혼용하지 않습니다.

루프백 캡처는 물리 케이블·무선 전파와 실제 이더넷 MAC 전달을 증명하지 않습니다. 이 시연의 1·2계층은 가상 루프백 경로의 역할로 설명합니다.
