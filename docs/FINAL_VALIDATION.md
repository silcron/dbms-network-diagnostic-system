# 최종 소스 검증 기록

## 2026-11-16 저장소 반영 전 재검증

- 기준: 전달받은 최종 ZIP과 동일한 바탕화면 소스. 실행 코드·스크립트·테스트를 반영하고 기존 저장소 문서를 보존했다.
- 환경: Windows, Python 3.14.7, PyMySQL 1.2.3, TShark 4.6.8.
- 명령: `python -B -m unittest discover -s tests -v`
- 결과: **131개 성공, 실패 0, 오류 0, 건너뜀 0**, 57.461초.
- 새 테스트 실행은 실제 DB 3개 장애를 다시 만드는 작업과 구분한다. 과거 LIVE 측정값은 아래 원본 자료를 근거로 한다.

## 최종 캡처 재검증

원본은 [final-run.pcapng](../evidence/final-20261114/final-run.pcapng)이며 2026-11-14의 132개 패킷이다. 진단 TCP 포트 62036, DB 1 포트 62014를 지정해 `scripts.check_packet_evidence`를 실행했다.

- 진단 LIVE stream 0: handshake 1·2·3번, Seq/Ack 유효
- DB 1 stream 1: handshake 10·12·15번, Seq/Ack 유효
- 진단 DEMO stream 8: handshake 114·115·116번, Seq/Ack 유효
- LIVE 결과 응답 104번: 전체 DOWN, DB 1 NORMAL / DB 2 WARNING(1045) / DB 3 DOWN(2003)
- DEMO 결과 응답 123번: 혼합 모의 상태, 전체 DOWN
- DB 1 통신에서 TLS 1.3 확인. 암호화된 SQL 내용을 패킷으로 직접 읽었다고 주장하지 않는다.
- 캡처 범위는 진단 TCP와 DB 세 포트이며 HTTP 구간은 포함되지 않는다. 링크 형식은 NULL/Loopback으로 실제 물리 LAN의 1·2계층 검증을 대신하지 않는다.

화면과 응답 대조는 [response-check.json](../evidence/final-20261114/response-check.json), 포트와 정상 준비 결과는 [ports.json](../evidence/final-20261114/ports.json), 상태·시간은 [results.json](../evidence/final-20261114/results.json)을 참고한다.

## 기존 팀원 기록과 구분

팀원 제공 최종 실행 환경은 2026-11-14 Windows / Python 3.14.3 / PyMySQL 1.2.3 / MariaDB 11.8.9 x64다. 11-15 자동 테스트 기록의 57.843초는 이번 11-16의 57.461초와 다른 실행이다.

두 PC Tailscale 실험은 별도 회차의 단일 DB 실험이다. 물리 동일 LAN 실험은 미실시이며 [LAN 문서](LAPTOP_LAN_TEST.md)는 준비 절차로 제공한다. 보고서·화면·캡처를 설명할 때 날짜와 회차를 구분한다.

## 웹 화면과 TCP 연동 재확인

2026-11-16 임시 로컬 포트에서 최신 TCP 서버와 웹 클라이언트를 실행하고 종료했다. HTTP 200, DEMO 혼합 상태의 DB별 NORMAL·WARNING·DOWN, OSI trace 7개, 각 DB의 finding·next_check 반환을 확인했다. 기존 실행 서버와 실제 DB는 변경하지 않았다.

[개별 테스트 결과](validation/unittest-20261116.txt)
