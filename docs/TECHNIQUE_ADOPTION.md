# 외부 기법 적용 기록

이 문서는 이 과제의 TCP/JSON·MariaDB 진단 구조를 유지하면서 참고한 공개 저장소의 기법을 어디까지 적용했는지 기록한다. 외부 저장소의 코드를 복사하거나 런타임 의존성으로 추가하지 않고, 현재 코드에 필요한 개념만 작은 범위로 옮긴다.

## 적용 경계

- Python 3.9 이상, 동기 TCP/JSON, 프로토콜 v2와 4바이트 big-endian 길이 헤더를 유지한다.
- 한 프레임의 최대 payload는 1 MiB이다. 이 프레이밍은 과제의 wire contract이므로 참고 예제의 다른 헤더 형식으로 바꾸지 않는다.
- `LIVE`는 실제 DBMS 연결·인증·`SELECT 1` 결과이고 `DEMO`는 명시적인 모의 결과이다. DEMO 성공을 LIVE 증거로 해석하지 않는다.
- PyShark, Prometheus/Grafana, Toxiproxy는 필수 설치 항목이 아니다. 기본 DEMO 실행은 저장소의 기존 Python 의존성만 사용한다.
- 캡처 파일과 DB 비밀번호·세션 토큰은 문서와 증거에 복사하지 않는다.

## 출처 → 기법 → 이 프로젝트의 적용

| 출처와 라이선스 | 참고한 구체적 기법 | 로컬 적용 위치와 경계 |
|---|---|---|
| [Real Python `libserver.py`](https://github.com/realpython/materials/blob/master/python-sockets-tutorial/libserver.py) · 저장소는 GitHub에서 MIT로 표시 | TCP 수신 버퍼를 누적하고 완전한 메시지 경계에서 JSON을 해석하는 방식 | `app/protocol.py`, `app/server.py`, `tests/test_protocol.py`에서 분할·합쳐진 수신과 전체 프레임 기한을 검증한다. 예제의 JSON header/2바이트 길이 형식은 채택하지 않고 현재 4바이트/1 MiB/v2 contract를 보존한다. |
| [PyMySQL `connections.py`](https://github.com/PyMySQL/PyMySQL/blob/main/pymysql/connections.py) · [MIT License](https://github.com/PyMySQL/PyMySQL/blob/main/LICENSE) | `connect_timeout`, `read_timeout`, `write_timeout`, 연결·인증·쿼리·정리의 경계 | `app/diagnostics.py`, `app/db_probe.py`, `app/diagnostic_models.py`, `tests/test_diagnostics.py`에서 연결/인증 시간을 하나의 connect 단계로, `SELECT 1`을 query 단계로 측정한다. 드라이버 내부에서 TCP/TLS/auth 시간을 다시 나눴다고 주장하지 않으며, 재시도·풀링·추가 raw TCP probe도 넣지 않는다. |
| [Blackbox Exporter `prober/tcp.go`](https://github.com/prometheus/blackbox_exporter/blob/master/prober/tcp.go), [`query_response.go`](https://github.com/prometheus/blackbox_exporter/blob/master/prober/query_response.go) · [Apache-2.0](https://github.com/prometheus/blackbox_exporter/blob/master/LICENSE) | 외부 probe의 성공 여부와 유한한 duration을 관측하는 방식 | `app/metrics.py`와 `app/web_client.py`의 웹 프로세스 관찰 지표에만 적용한다. exporter 코드를 가져오지 않고, 세션 ID·host/user/database·예외 문자열을 label로 만들지 않는다. |
| [MySQLd Exporter `collector/exporter.go`](https://github.com/prometheus/mysqld_exporter/blob/main/collector/exporter.go) · [Apache-2.0](https://github.com/prometheus/mysqld_exporter/blob/main/LICENSE) | 수집기 경계와 계정/수집 범위를 분리하는 방식 | 진단 웹 프로세스가 관찰한 요청만 집계하고 DB exporter를 추가하지 않는다. `/metrics` scrape는 DB를 다시 접속하지 않으며, 프로세스 재시작 시 지표가 초기화된다. |
| [PyShark `capture.py`](https://github.com/KimiNewt/pyshark/blob/master/src/pyshark/capture/capture.py) · [MIT License](https://github.com/KimiNewt/pyshark/blob/master/LICENSE.txt) | TShark subprocess와 display/filter/stream 필드를 이용한 캡처 분석 경계 | `scripts/check_packet_evidence.py`, `scripts/packet_analysis.py`, `tests/test_packet_analysis.py`에서 TShark를 직접 제한 시간·출력 상한과 함께 호출한다. 각 subprocess는 15초, stdout/stderr 각각 4 MiB이며 캡처 분석은 최대 128개 매칭 TCP stream과 decoded bytes 8 MiB까지다. PyShark를 설치하거나 일반 TCP reassembler를 새로 만들지 않는다. TLS 아래의 SQL은 읽을 수 있는 것으로 표시하지 않는다. |
| [Toxiproxy README](https://github.com/Shopify/toxiproxy/blob/main/README.md) · [MIT License](https://github.com/Shopify/toxiproxy/blob/main/LICENSE) | 명시적인 proxy CRUD와 latency/timeout/reset_peer/slicer toxic | `scripts/check_fault_scenarios.py`와 adapter가 이미 실행 중인 로컬 endpoint에서 유일한 이름의 proxy만 만들고, 자기 proxy/toxic만 `finally`에서 제거한다. exit 0은 fault/recovery 검증 성공, exit 1은 assertion 또는 owned-proxy 계약 실패, exit 2는 API 부재 같은 prerequisite skip이다. 전역 reset, 기존 proxy 삭제, MariaDB 종료, 자동 설치는 하지 않는다. |

표의 라이선스 표기는 각 upstream 저장소의 [repository metadata](https://github.com/realpython/materials) 또는 연결한 `LICENSE`/README 원문을 기준으로 적었다. 이 프로젝트에는 출처 코드의 복사본이나 해당 도구의 필수 배포물이 들어 있지 않다. 향후 코드 조각을 복사해 배포할 때는 해당 라이선스의 저작권·고지·NOTICE 조건을 별도로 검토한다.

## 진단 계약과 시간 의미

기존 응답 key는 유지하고 LIVE 응답에 다음 선택적 필드를 추가한다.

| 필드 | 허용 값과 의미 |
|---|---|
| `failed_phase` | `null`, `config`, `dependency`, `connect`, `query` 중 하나. 성공은 `null`이다. |
| `error_kind` | `none`, `config`, `dependency`, `network`, `timeout`, `auth`, `database`, `query`, `internal` 중 하나. |
| `db_errno` | 정수 또는 `null`; 비밀번호·원문 예외는 넣지 않는다. |
| `query_ms` | 유한한 0 이상 수 또는 `null`; query 단계가 실행되지 않으면 `null`이다. |
| `total_ms` | 유한한 0 이상 수; 전체 진단 elapsed time이다. |

`database_connect_ms`는 PyMySQL connect/auth 호출의 시간이고, `database_rtt_ms`는 기존 호환 key로서 전체 진단 시간 의미를 유지한다. 느리지만 성공한 LIVE 결과는 `WARNING`일 수 있으며 `failed_phase=null`, `error_kind=none`이다. DEMO에는 실제 DB 시간을 만들지 않고 새 timing/phase 필드를 생략하거나 명시적으로 simulated로 표시한다.

환경 설정의 기본값과 범위는 다음과 같다.

- TCP idle 60초, 전체 frame 수신 10초, send 5초.
- `DiagnosticClient` timeout을 생략하면 `DIAGNOSTIC_TIMEOUT_SECONDS`를 읽고 기본 10초를 사용한다. 값은 유한한 양수이고 최대 120초이며, 생성자에 명시한 timeout이 환경값보다 우선한다.
- DB connect/read/write timeout은 `DB_CONNECT_TIMEOUT_SECONDS`, `DB_READ_TIMEOUT_SECONDS`, `DB_WRITE_TIMEOUT_SECONDS`가 각각 기본 3초이고 0보다 큰 유한한 값, 최대 30초이다. 이 값은 **각 DB 연산의 제한**이며 전체 진단이 반드시 10초 안에 끝난다는 보장은 아니다. 3초보다 큰 DB 예산을 쓰려면 client timeout도 충분히 크게 설정한다.
- `DB_WARNING_MS`는 기본값 500ms인 0보다 큰 유한한 값이다.
- 웹 요청 body는 최대 4096 bytes, 전체 body 수신 기한은 2초이다.

TCP client가 먼저 만료되면 해당 socket/session을 정리하고 HTTP 계층은 DEMO fallback 없이 upstream timeout(504)을 반환한다. 설정·의존성·네트워크·인증·쿼리 실패의 의미를 섞지 않으며, 진단 결과를 얻은 LIVE `WARNING`/`DOWN`은 유효한 진단이므로 HTTP 200으로 전달한다. 진단 자체를 얻지 못한 경우에만 HTTP 오류를 사용한다.

## HTTP와 metrics

경계 검증 오류는 안전한 메시지와 안정적인 code를 함께 반환한다. 요청 오류 code는 `invalid_request`, `invalid_mode`, `invalid_scenario`, `invalid_session`, `unsupported_type`이고, HTTP upstream 오류 code는 `upstream_failure`, `upstream_timeout`, `internal_error`이다. raw exception과 credential은 노출하지 않는다. metrics label에는 session ID, host, user, database 식별자와 raw exception을 넣지 않으며, 기존 성공 결과의 `database` 메타데이터 key는 호환성을 위해 유지한다.

`GET /metrics`는 웹 프로세스가 실제로 관찰한 요청만 집계한다. label 값은 미리 정한 유한 집합이다. 웹 listener의 pending accept queue 설정은 32이지만 처리량·동시성 capacity 보장은 아니며, bounded request backlog 설정으로만 문서화한다.

```text
dbms_diagnostic_requests_total{mode,outcome}
dbms_diagnostic_results_total{mode,status,error_kind}
dbms_diagnostic_duration_seconds_sum{mode}
dbms_diagnostic_duration_seconds_count{mode}
dbms_diagnostic_last_success{mode}
```

`mode`는 `live`, `demo`, `unknown`, `outcome`은 `success`, `invalid_request`, `upstream_failure`, `upstream_timeout`, `internal_error`로 제한한다. `status`와 `error_kind`도 진단 contract의 고정 집합만 사용한다. duration은 유한한 관측에 대해서만 합·count를 늘리고, `last_success`는 해당 mode에 관측이 생긴 뒤에만 내보낸다. `DEMO` 관측은 `LIVE` 건강 상태를 갱신하지 않는다. 직접 TCP client와 Tkinter 요청은 이 웹 metrics 범위에 포함되지 않는다.

## 선택적 검증 도구

Toxiproxy를 사용할 때는 이미 실행 중인 로컬 API와 테스트용 loopback upstream/listen port를 명시한다. 예시는 다음과 같으며 `<...>`는 이번 실행에서 확보한 소유 포트로 치환한다. 이미 실행 중인 API만 사용하고 설치·daemon 시작은 하지 않는다. exit 0은 fault/recovery 검증 성공, exit 1은 assertion 또는 owned-proxy 계약 실패, exit 2는 API 부재 같은 prerequisite skip이다.

```powershell
python -B -m scripts.check_fault_scenarios --toxiproxy-url http://127.0.0.1:8474 --target diagnostic --mode demo --upstream 127.0.0.1:<owned_tcp_port> --listen 127.0.0.1:<free_proxy_port>
```

기존 proxy와 이름이 충돌하거나 API/DB prerequisite가 없으면 검증 성공으로 가장하지 않고 명확한 blocked/skip 결과를 낸다. 성공 여부만 toxic 생성 API response로 판단하지 않고 baseline 회복, 관찰된 진단 결과와 bounded elapsed time까지 확인한다.

기존 캡처 검증은 그대로 둔다.

```powershell
python -B -m scripts.check_packet_evidence
```

새 stream 분석은 캡처와 진단 port를 명시한다. `--help` 출력의 실제 옵션을 기준으로 실행하며, TShark가 없거나 캡처가 잘못되면 성공으로 처리하지 않는다.

```powershell
python -B -m scripts.check_packet_evidence --capture demo_tcp_9090_20261005.pcapng --diagnostic-port 9090
python -B -m scripts.check_packet_evidence --capture live_mariadb_3307_20261005.pcapng --diagnostic-port 9090 --db-port 3307
```

분석은 TShark follow/tcp/raw stream에서 양방향 payload를 제한된 크기로 읽어 4바이트 length-prefixed 메시지를 분할·병합·다중 메시지까지 확인한다. 각 subprocess는 15초, stdout/stderr 각각 4 MiB이며 캡처 전체는 최대 128개 매칭 TCP stream과 decoded bytes 8 MiB까지다. SYN/SYN-ACK/ACK 관계와 재전송·순서 이상·reset·lost-segment 관찰을 애플리케이션 status와 별도로 보고한다. TLS가 사용된 LIVE 캡처에서 SQL 텍스트를 복원했다고 말하지 않으며, loopback 캡처의 전송 이상을 자동으로 DB DOWN으로 단정하지 않는다.

발표 직전 DB 없는 환경은 다음 DEMO 전용 gate를 사용한다. `<owned_tcp_port>`와 `<owned_http_port>`는 이번 gate가 시작한 서비스 port로 바꾼다.

```powershell
python -B -m scripts.check_presentation_demo --demo-only --host 127.0.0.1 --demo-port <owned_tcp_port> --web-port <owned_http_port> --require-current-server
```

기존 full gate는 LIVE 설정이 있을 때만 실행한다. `scripts/check_live.py`는 별도 help 옵션을 제공하지 않으므로, 문서에 있는 실제 실행 형태를 구성된 local DB와 실행 중인 LIVE 서버가 있을 때만 사용한다. 실제 MariaDB 증거는 명시적 local DB 설정과 실제 연결이 실행된 경우에만 기록한다.

## 검증 증거와 한계

Task 6 증거는 `.omo/evidence/upstream-adoption/task6-docs.md`에 각 기준별로 명령, exit code, 관찰한 stdout/stderr 요약, 산출물 경로와 prerequisite를 기록한다. 테스트가 통과했어도 실제 TCP/HTTP/CLI 산출물이나 캡처 결과가 없으면 LIVE·Toxiproxy·TShark 성공으로 표현하지 않는다.

계속 적용되는 한계는 다음과 같다.

- connect timing 하나만으로 TCP, TLS, 인증 단계를 독립 측정했다고 말할 수 없다.
- 암호화된 MariaDB 패킷에서는 SQL 본문을 읽을 수 없다.
- loopback 시연은 실제 Ethernet/Wi-Fi 전송을 증명하지 않으며 OSI 1·2계층 설명은 역할 대응이다.
- Toxiproxy slicer는 전달 stress를 주입하지만 애플리케이션의 `recv()` 경계를 정확히 보장하지 않는다.
- metrics는 웹 프로세스와 그 process lifetime의 관측만 나타내며, 재시작 시 reset된다.
