const button = document.querySelector('#diagnose');
const notice = document.querySelector('#notice');
const result = document.querySelector('#result');
const modeChoices = document.querySelectorAll('input[name="mode"]');
const scenario = document.querySelector('#scenario');
const modeHelp = document.querySelector('#mode-help');
const portInput = document.querySelector('#port');
const hostInput = document.querySelector('#host');
const route = document.querySelector('#route');
const trace = document.querySelector('#osi');
const traceState = document.querySelector('#trace-state');
const traceNote = document.querySelector('.trace-note');
const buttonLabel = button.querySelector('.button-label');
let usingTemporaryDemoPort = false;

function setText(selector, value) { document.querySelector(selector).textContent = value; }
function selectedMode() { return document.querySelector('input[name="mode"]:checked').value; }
function isDuration(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0; }
function formatCheckedAt(value) {
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})/);
  return match ? `${match[1]}.${match[2]}.${match[3]} ${match[4]}` : value;
}
function localTime() {
  const now = new Date();
  const pad = value => String(value).padStart(2, '0');
  return `${now.getFullYear()}.${pad(now.getMonth() + 1)}.${pad(now.getDate())} ${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
}
function capturePort() {
  setText('#capture-filter', `tcp.port == ${portInput.value || '9090'}`);
}
class DiagnosisError extends Error {
  constructor(code, details = {}) {
    super(code);
    this.code = code;
    this.details = details;
  }
}
function validateTarget(host, rawPort) {
  if (!host) throw new DiagnosisError('invalid_address', {input: '(비어 있음)'});
  if (/^[\d.]+$/.test(host)) {
    const parts = host.split('.');
    if (parts.length !== 4 || parts.some(part => !/^\d{1,3}$/.test(part) || Number(part) > 255)) {
      throw new DiagnosisError('invalid_address', {input: host});
    }
  }
  if (!/^\d+$/.test(rawPort) || Number(rawPort) < 1 || Number(rawPort) > 65535) {
    throw new DiagnosisError('invalid_port', {input: rawPort || '(비어 있음)'});
  }
  return Number(rawPort);
}
function failureGuide(error) {
  const info = error.details || {};
  const guides = {
    invalid_address: ['서버 주소 형식을 확인할 수 없습니다.', 'IPv4 주소는 0~255 사이 숫자 네 개를 점으로 구분해야 합니다. 주소를 다시 입력하세요.'],
    invalid_port: ['포트 번호가 올바르지 않습니다.', '진단 서버의 TCP 포트를 1~65535 범위에서 입력하세요.'],
    name_resolution: ['입력한 서버 주소를 IP 주소로 변환하지 못했습니다.', '주소 오타 또는 이름 확인(DNS) 설정을 확인하세요.'],
    connection_refused: ['설정한 주소·포트에서 TCP 연결이 거부되었습니다.', '포트 번호와 진단 서버 실행 상태를 확인하세요. 어느 쪽이 원인인지는 이 응답만으로 단정할 수 없습니다.'],
    timeout: ['진단 서버가 제한 시간 안에 응답하지 않았습니다.', '서버 실행 상태와 네트워크 경로·방화벽을 확인하세요.'],
    connection_reset: ['연결 중 상대편이 TCP 연결을 끊었습니다.', '서버 로그와 해당 포트의 서비스 종류를 확인하세요.'],
    protocol_error: ['TCP 연결 이후 진단 서버의 응답 형식을 확인할 수 없었습니다.', '해당 포트에서 이 프로그램의 진단 서버가 실행 중인지 확인하세요.'],
    invalid_request: ['진단 요청 값이 서버에서 거부되었습니다.', '서버 주소·포트·진단 방식을 확인하세요.'],
    internal_error: ['웹 클라이언트 내부에서 진단을 완료하지 못했습니다.', '웹 클라이언트 로그를 확인하세요.'],
    network_error: ['진단 서버와 TCP 통신을 완료하지 못했습니다.', '주소·포트·네트워크 연결을 확인하세요.'],
    upstream_failure: ['진단 서버와 통신을 완료하지 못했습니다.', '포트와 서버 실행 상태를 먼저 확인하세요.'],
  };
  const [reason, next] = guides[error.code] || guides.network_error;
  const evidence = [];
  if (info.input !== undefined) evidence.push(`입력값 ${info.input}`);
  if (info.target) evidence.push(`요청 대상 ${info.target}`);
  if (info.phase === 'connect') evidence.push('TCP 연결·세션 시작 단계');
  if (info.phase === 'diagnose') evidence.push('진단 요청·응답 단계');
  if (Number.isInteger(info.system_errno)) evidence.push(`운영체제 오류 번호 ${info.system_errno}`);
  if (Number.isInteger(info.httpStatus) && ['upstream_failure', 'invalid_request', 'internal_error'].includes(error.code)) {
    evidence.push(`웹 응답 HTTP ${info.httpStatus}`);
  }
  return {reason, evidence: evidence.join(' · ') || '진단 요청이 완료되지 않음', next};
}
function setNotice(message, state = 'success') {
  notice.querySelector('.notice-text').textContent = message;
  notice.classList.toggle('is-ready', state === 'ready');
  notice.classList.toggle('is-running', state === 'running');
  notice.classList.toggle('is-error', state === 'error');
  notice.classList.toggle('is-stale', state === 'stale');
}
function displayOsiItem(item) {
  // Already-localized responses from the new server need no conversion.
  if (/[가-힣]/.test(item.layer)) return item;
  const number = Number.parseInt(item.layer, 10);
  const names = {7:'응용', 6:'표현', 5:'세션', 4:'전송', 3:'네트워크', 2:'데이터링크', 1:'물리'};
  const port = item.application.match(/destination port (\d+)/)?.[1] || portInput.value;
  const addresses = item.application.match(/IPv4 source ([\d.]+), destination ([\d.]+)/);
  const loopback = item.application.includes('Loopback') || item.application.includes('loopback');
  const mac = item.application.match(/(?:[0-9A-F]{2}:){5}[0-9A-F]{2}/i)?.[0];
  const descriptions = {
    7: '진단 요청·응답(hello/diagnose/logout); 응용 메시지에 4바이트 길이 헤더를 붙여 경계를 구분합니다.',
    6: '진단 데이터를 JSON으로 직렬화하고 UTF-8로 인코딩합니다.',
    5: '서버가 발급한 세션 ID를 요청마다 확인합니다(응용 프로그램의 세션 기능).',
    4: `TCP 소켓으로 연결하고 목적지 포트 ${port}을 사용합니다.`,
    3: addresses ? `IPv4 출발지 ${addresses[1]} → 목적지 ${addresses[2]}` : item.application,
    2: loopback ? '루프백 인터페이스를 사용하므로 이 경로에는 이더넷 MAC 프레임이 없습니다.' : `로컬 인터페이스 MAC ${mac || '확인 필요'} (참고 정보)`,
    1: loopback ? '가상 루프백 경로이므로 케이블·무선 전파를 통한 실제 전송은 없습니다.' : '활성 인터페이스에 따라 이더넷 케이블 또는 Wi-Fi 전파로 전송됩니다.'
  };
  return {layer: `${number} ${names[number] || '기타'} 계층`, application: descriptions[number] || item.application};
}
function renderTrace(items) {
  trace.replaceChildren(...items.map(rawItem => {
    const item = displayOsiItem(rawItem);
    const line = document.createElement('li');
    const number = Number.parseInt(item.layer, 10);
    if (number >= 1 && number <= 4) line.classList.add('network-focus');
    const layer = document.createElement('b'); layer.textContent = item.layer;
    const explanation = document.createElement('span'); explanation.textContent = item.application;
    line.append(layer, explanation);
    return line;
  }));
  traceState.classList.add('hidden');
  traceNote.hidden = false;
}
function showUnavailableTrace(message, error) {
  const host = hostInput.value.trim();
  const port = portInput.value.trim();
  const loopback = host === 'localhost' || /^127\./.test(host);
  const notSent = ['invalid_address', 'invalid_port', 'invalid_request'].includes(error.code);
  const checks = notSent ? [
    ['4 전송 계층', '입력 단계에서 중단: TCP 연결 시도 없음'],
    ['3 네트워크 계층', `입력 주소 ${host || '(비어 있음)'} · IP 경로 확인 전`],
    ['2 데이터링크 계층', '패킷 전송 시도 없음'],
    ['1 물리 계층', '패킷 전송 시도 없음'],
  ] : [
    ['4 전송 계층', `목적지 TCP 포트 ${port || '(미입력)'} · ${error.details?.phase === 'diagnose' ? '연결 후 진단 응답 실패' : '연결 결과를 Wireshark로 확인 필요'}`],
    ['3 네트워크 계층', `설정한 목적지 주소 ${host || '(비어 있음)'} · 실제 패킷의 목적지 IP는 캡처로 확인`],
    ['2 데이터링크 계층', loopback ? '127.0.0.0/8 루프백 경로: 이더넷 프레임 없음' : '사용한 LAN 인터페이스는 캡처로 확인 필요'],
    ['1 물리 계층', loopback ? '루프백은 가상 경로: 케이블·Wi‑Fi 전송 없음' : '실제 전송 매체는 사용 환경에서 확인 필요'],
  ];
  trace.replaceChildren(...checks.map(([layerText, description]) => {
    const line = document.createElement('li');
    line.classList.add('network-focus');
    const layer = document.createElement('b'); layer.textContent = layerText;
    const explanation = document.createElement('span'); explanation.textContent = description;
    line.append(layer, explanation);
    return line;
  }));
  traceState.textContent = message;
  traceState.classList.remove('hidden');
  traceNote.hidden = true;
}
function nextDatabaseCheck(db) {
  if (db.mode === 'DEMO') return '실제 접속 상태가 필요한 경우 LIVE로 다시 진단하세요.';
  const checks = {
    config:'진단 서버에서 이 DB의 접속 설정을 확인하세요.',
    dependency:'진단 서버의 DB 연결 모듈 설치 상태를 확인하세요.',
    network:'DB 주소·포트와 MariaDB 서비스 실행 상태를 확인하세요.',
    timeout:'MariaDB 응답과 네트워크 지연을 점검하세요.',
    auth:'DB 사용자 이름·비밀번호·접속 권한을 확인하세요.',
    database:'DB_NAME과 데이터베이스 접근 권한을 확인하세요.',
    query:'SELECT 1 실행 결과와 쿼리 권한을 확인하세요.',
    internal:'진단 서버 로그를 확인하세요.',
  };
  return checks[db.error_kind] || '필요하면 Wireshark에서 진단 서버의 TCP 연결을 확인하세요.';
}
function renderTargetDetail(db) {
  setText('#detail-title', db.target_id ? `DB ${db.target_id} · 결과 해석` : '결과 해석');
  setText('#database', db.database);
  setText('#detail', db.detail);
  const simulated = db.mode === 'DEMO' ? '모의 ' : '';
  const facts = [];
  if (db.mode === 'DEMO') facts.push('선택한 시나리오로 생성한 모의 결과 · 실제 DB 미접속');
  else facts.push('진단 서버 응답 수신');
  const phaseNames = {config:'설정', dependency:'의존성', connect:'DB 연결·인증', query:'SELECT 1'};
  const errorNames = {config:'설정', dependency:'의존성', network:'네트워크', timeout:'시간 초과', auth:'인증', database:'데이터베이스', query:'쿼리', internal:'내부 처리'};
  if (phaseNames[db.failed_phase]) facts.push(`실패 단계 ${phaseNames[db.failed_phase]}`);
  if (errorNames[db.error_kind]) facts.push(`오류 분류 ${errorNames[db.error_kind]}`);
  if (Number.isInteger(db.db_errno) && db.db_errno >= 0) facts.push(`MariaDB 오류 번호 ${db.db_errno}`);
  if (db.mode === 'LIVE' && db.status === 'NORMAL') facts.push('DB 로그인·SELECT 1 성공');
  setText('#evidence', facts.join(' · '));
  setText('#next-check', db.next_check || nextDatabaseCheck(db));
  const timings = [];
  if (isDuration(db.database_connect_ms)) timings.push(`${simulated}DB 연결·인증 ${db.database_connect_ms} ms`);
  if (isDuration(db.query_ms)) timings.push(`${simulated}SELECT 1 ${db.query_ms} ms`);
  if (isDuration(db.total_ms)) timings.push(`${simulated}진단 전체 ${db.total_ms} ms`);
  else if (isDuration(db.database_rtt_ms)) timings.push(`${simulated}DB 진단 전체 ${db.database_rtt_ms} ms`);
  setText('#dbtiming', timings.join(' · '));
}
function renderDatabaseResult(data) {
  const db = data.result;
  const targets = data.results || [db];
  setText('#status-label', targets.length > 1 ? '전체 상태 · 가장 높은 심각도' : '진단 상태');
  setText('#status', db.status);
  setText('#mode', db.mode);
  setText('#mode-label', '실행 모드');
  setText('#rtt', `${data.client_rtt_ms} ms`);
  setText('#checked', formatCheckedAt(data.checked_at));
  setText('#checked-label', '확인 시각');
  renderTargetChoices(targets, db, data.feedback_summary);
  result.dataset.status = db.status.toLowerCase();
  result.classList.remove('hidden');
  route.classList.add('is-complete');
  route.classList.toggle('is-db-error', db.status === 'DOWN');
  renderTrace(data.osi_trace);
  setText('#capture-note', 'Wireshark의 루프백 인터페이스에서 이 포트의 SYN → SYN/ACK → ACK를 확인하세요. 화면 결과만으로 패킷 검증을 완료했다고 판단하지 않습니다.');
  setNotice(db.mode === 'DEMO'
    ? `${usingTemporaryDemoPort ? '임시 시연 서버(9091) · ' : ''}DEMO 결과입니다. 실제 DBMS 연결 여부는 확인하지 않았습니다.`
    : 'LIVE 진단 결과입니다. 판정 근거와 다음 점검 항목을 확인하세요.');
}
function renderFailure(error, requestedMode, target) {
  document.querySelector('#targets-panel').hidden = true;
  document.querySelector('#targets').replaceChildren();
  setText('#feedback-summary', '');
  setText('#detail-title', '결과 해석');
  setText('#status-label', '진단 상태');
  const guide = failureGuide(error);
  const invalid = error.code === 'invalid_address' || error.code === 'invalid_port' || error.code === 'invalid_request';
  setText('#status', invalid ? '입력 오류' : '통신 실패');
  setText('#mode', requestedMode.toUpperCase());
  setText('#mode-label', '선택 모드');
  setText('#rtt', '측정 불가');
  setText('#checked', localTime());
  setText('#checked-label', '시도 시각');
  setText('#database', `진단 대상 ${target}`);
  setText('#detail', guide.reason);
  setText('#evidence', guide.evidence);
  setText('#next-check', guide.next);
  setText('#dbtiming', '');
  result.dataset.status = invalid ? 'invalid' : 'error';
  result.classList.remove('hidden');
  route.classList.toggle('is-error', !invalid);
  showUnavailableTrace(invalid
    ? '입력 단계에서 중단되어 진단 서버의 OSI 추적 로그가 없습니다. 아래 1–4계층은 미시도 상태입니다.'
    : '진단 서버의 OSI 추적을 받지 못했습니다. 아래 1–4계층은 입력값 기준 점검 항목이며 패킷 검증 결과가 아닙니다.', error);
  setText('#capture-note', invalid
    ? '입력 오류로 TCP 연결을 시도하지 않았으므로 이 진단에 해당하는 패킷은 없습니다.'
    : 'Wireshark에서 연결 거부 시 SYN·RST, 무응답 시 SYN 재전송 등이 나타날 수 있습니다. 실제 패킷을 확인한 뒤 원인을 판단하세요.');
  setNotice(invalid ? '입력값을 수정한 뒤 다시 진단하세요.' : '진단이 완료되지 않았습니다. 결과 영역에서 관측 근거와 점검 항목을 확인하세요.', 'error');
}
function updateMode() {
  const isDemo = selectedMode() === 'demo';
  if (!isDemo && usingTemporaryDemoPort) {
    portInput.value = '9090';
    usingTemporaryDemoPort = false;
  }
  scenario.disabled = !isDemo;
  setText('#path-target', isDemo ? '모의 상태' : 'MariaDB 최대 3개');
  setText('#path-action', isDemo ? '실제 DB 연결 없음' : '접속 · 로그인 · SELECT 1');
  setText('#tcp-port-label', `TCP ${portInput.value} · 세션 확인`);
  modeHelp.textContent = isDemo
    ? 'DEMO는 발표용 모의 결과입니다. 실제 MariaDB 연결 상태나 장애를 증명하지 않습니다.'
    : 'MariaDB 최대 3개의 로그인과 쿼리 응답을 점검합니다.';
  const hasResult = !result.classList.contains('hidden');
  result.classList.toggle('is-stale', hasResult);
  route.classList.remove('is-complete', 'is-error', 'is-db-error');
  capturePort();
  setNotice(hasResult
    ? '진단 방식이 변경되었습니다. 아래는 이전 결과입니다. 다시 진단하면 새 결과가 표시됩니다.'
    : (isDemo ? '시연할 모의 상황을 선택한 뒤 진단을 시작하세요.' : '실제 MariaDB 상태를 확인할 준비가 되었습니다.'), hasResult ? 'stale' : 'ready');
}
modeChoices.forEach(choice => choice.addEventListener('change', updateMode));
function markStale() {
  if (!result.classList.contains('hidden')) {
    result.classList.add('is-stale');
    setNotice('설정이 변경되었습니다. 아래는 이전 결과입니다. 다시 진단하면 새 결과가 표시됩니다.', 'stale');
  }
}
hostInput.addEventListener('input', markStale);
portInput.addEventListener('input', () => {
  setText('#tcp-port-label', `TCP ${portInput.value} · 세션 확인`);
  capturePort();
  markStale();
});
scenario.addEventListener('change', markStale);
updateMode();

async function requestDiagnosis(host, port, mode, demoScenario) {
  let response;
  try {
    response = await fetch('/api/diagnose', {
      method: 'POST', headers: {'Content-Type':'application/json; charset=utf-8'},
      body: JSON.stringify({host, port, mode, demo_scenario: mode === 'demo' ? demoScenario : 'normal'})
    });
  } catch {
    throw new DiagnosisError('network_error', {target: `${host}:${port}`});
  }
  let data;
  try { data = await response.json(); }
  catch { throw new DiagnosisError('protocol_error', {target: `${host}:${port}`, httpStatus: response.status}); }
  if (!response.ok) {
    const legacy = String(data.error || '');
    const legacyReason = /^ConnectionRefusedError:/.test(legacy) ? 'connection_refused'
      : /^gaierror:/.test(legacy) ? 'name_resolution'
      : /^TimeoutError:/.test(legacy) ? 'timeout'
      : /^ConnectionResetError:/.test(legacy) ? 'connection_reset' : null;
    const legacyErrno = legacy.match(/\[WinError (\d+)\]/);
    throw new DiagnosisError(data.reason_code || legacyReason || (data.code === 'upstream_timeout' ? 'timeout' : data.code || 'upstream_failure'), {
      target: `${host}:${port}`,
      phase: data.phase || (legacyReason ? 'connect' : undefined),
      system_errno: Number.isInteger(data.system_errno) ? data.system_errno : legacyErrno ? Number(legacyErrno[1]) : undefined,
      httpStatus: response.status,
    });
  }
  if (data.type === 'error') throw new DiagnosisError('protocol_error', {target: `${host}:${port}`, phase: 'diagnose'});
  return data;
}

function setSettingsBusy(busy) {
  hostInput.disabled = busy;
  portInput.disabled = busy;
  modeChoices.forEach(choice => { choice.disabled = busy; });
  scenario.disabled = busy || selectedMode() !== 'demo';
}

button.addEventListener('click', async () => {
  button.disabled = true;
  setSettingsBusy(true);
  buttonLabel.textContent = '진단 중';
  button.setAttribute('aria-busy', 'true');
  route.classList.remove('is-complete', 'is-error', 'is-db-error');
  route.classList.add('is-running');
  result.classList.remove('is-stale');
  result.classList.add('hidden');
  const requestedMode = selectedMode();
  setNotice('TCP 서버에 연결하고 DBMS 상태를 확인하는 중입니다…', 'running');
  let target = `${hostInput.value.trim()}:${portInput.value.trim()}`;
  try {
    const host = hostInput.value.trim();
    const port = validateTarget(host, portInput.value.trim());
    target = `${host}:${port}`;
    let data = await requestDiagnosis(host, port, requestedMode, scenario.value);
    if (requestedMode === 'demo' && data.result.mode === 'LIVE' && port === 9090 && ['127.0.0.1', 'localhost'].includes(host)) {
      portInput.value = '9091';
      usingTemporaryDemoPort = true;
      setText('#tcp-port-label', 'TCP 9091 · 세션 확인');
      capturePort();
      target = `${host}:9091`;
      data = await requestDiagnosis(host, 9091, requestedMode, scenario.value);
    }
    const db = data.result;
    if (db.mode.toLowerCase() !== requestedMode) {
      throw new DiagnosisError('protocol_error', {target, phase: 'diagnose'});
    }
    renderDatabaseResult(data);
  } catch (error) {
    const failure = error instanceof DiagnosisError ? error : new DiagnosisError('internal_error', {target});
    renderFailure(failure, requestedMode, failure.details.target || target);
  } finally {
    route.classList.remove('is-running');
    setSettingsBusy(false);
    button.disabled = false;
    buttonLabel.textContent = '진단 시작';
    button.removeAttribute('aria-busy');
  }
});
