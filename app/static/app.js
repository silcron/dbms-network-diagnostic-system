const button = document.querySelector('#diagnose');
const notice = document.querySelector('#notice');
const result = document.querySelector('#result');
const modeChoices = document.querySelectorAll('input[name="mode"]');
const scenario = document.querySelector('#scenario');
const modeHelp = document.querySelector('#mode-help');
const portInput = document.querySelector('#port');
let usingTemporaryDemoPort = false;

function setText(selector, value) { document.querySelector(selector).textContent = value; }
function selectedMode() { return document.querySelector('input[name="mode"]:checked').value; }
function isDuration(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0; }
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
function updateMode() {
  const isDemo = selectedMode() === 'demo';
  if (!isDemo && usingTemporaryDemoPort) {
    portInput.value = '9090';
    usingTemporaryDemoPort = false;
  }
  scenario.disabled = !isDemo;
  setText('#path-target', isDemo ? '모의 상태' : 'MariaDB');
  setText('#path-action', isDemo ? '실제 DB 연결 없음' : '접속 · 로그인 · SELECT 1');
  setText('#tcp-port-label', `TCP ${portInput.value} · 세션 확인`);
  modeHelp.textContent = isDemo
    ? 'DEMO는 발표용 모의 결과입니다. 실제 MariaDB 연결 상태나 장애를 증명하지 않습니다.'
    : 'LIVE는 실제 MariaDB에 연결·로그인하고 SELECT 1을 실행합니다.';
  notice.textContent = result.classList.contains('hidden')
    ? (isDemo ? '시연할 모의 상황을 선택한 뒤 진단을 시작하세요.' : '실제 MariaDB 상태를 확인할 준비가 되었습니다.')
    : '진단 방식이 변경되었습니다. 아래는 이전 결과이며, 새 방식의 결과를 보려면 「DBMS 진단 시작」을 다시 누르세요.';
}
modeChoices.forEach(choice => choice.addEventListener('change', updateMode));
portInput.addEventListener('input', () => setText('#tcp-port-label', `TCP ${portInput.value} · 세션 확인`));
updateMode();

async function requestDiagnosis(host, port, mode, demoScenario) {
  const response = await fetch('/api/diagnose', {
    method: 'POST', headers: {'Content-Type':'application/json; charset=utf-8'},
    body: JSON.stringify({host, port, mode, demo_scenario: demoScenario})
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '진단 요청에 실패했습니다.');
  if (data.type === 'error') throw new Error(data.message || 'TCP 서버가 요청을 거부했습니다.');
  return data;
}

button.addEventListener('click', async () => {
  button.disabled = true;
  const requestedMode = selectedMode();
  notice.textContent = 'TCP 서버에 연결하고 DBMS 상태를 확인하는 중입니다…';
  try {
    const host = document.querySelector('#host').value;
    const port = Number(portInput.value);
    let data = await requestDiagnosis(host, port, requestedMode, scenario.value);
    if (requestedMode === 'demo' && data.result.mode === 'LIVE' && port === 9090 && ['127.0.0.1', 'localhost'].includes(host)) {
      data = await requestDiagnosis(host, 9091, requestedMode, scenario.value);
      portInput.value = '9091';
      usingTemporaryDemoPort = true;
      setText('#tcp-port-label', 'TCP 9091 · 세션 확인');
    }
    const db = data.result;
    if (db.mode.toLowerCase() !== requestedMode) {
      throw new Error('선택한 진단 방식과 서버 응답이 다릅니다. 서버를 재시작한 뒤 다시 시도하세요.');
    }
    setText('#status', db.status);
    setText('#mode', db.mode);
    setText('#rtt', `${data.client_rtt_ms} ms`);
    setText('#checked', data.checked_at);
    setText('#database', db.database);
    setText('#detail', db.detail);
    const facts = [];
    const simulated = db.mode === 'DEMO' ? '모의 ' : '';
    if (isDuration(db.database_connect_ms)) facts.push(`${simulated}DB 연결·인증 ${db.database_connect_ms} ms`);
    if (isDuration(db.query_ms)) facts.push(`${simulated}SELECT 1 ${db.query_ms} ms`);
    if (isDuration(db.total_ms)) {
      facts.push(`${simulated}진단 전체 ${db.total_ms} ms`);
    } else if (isDuration(db.database_rtt_ms)) {
      facts.push(`${simulated}DB 진단 전체 ${db.database_rtt_ms} ms`);
    }
    const phaseNames = {config:'설정', dependency:'의존성', connect:'DB 연결·인증', query:'SELECT 1'};
    const errorNames = {
      config:'설정', dependency:'의존성', network:'네트워크', timeout:'시간 초과',
      auth:'인증', database:'데이터베이스', query:'쿼리', internal:'내부 처리'
    };
    if (phaseNames[db.failed_phase]) facts.push(`${simulated}실패 단계 ${phaseNames[db.failed_phase]}`);
    if (errorNames[db.error_kind]) facts.push(`${simulated}오류 원인 ${errorNames[db.error_kind]}`);
    if (Number.isInteger(db.db_errno) && db.db_errno >= 0) facts.push(`${simulated}DB 오류 번호 ${db.db_errno}`);
    setText('#dbtiming', facts.join(' · '));
    result.dataset.status = db.status.toLowerCase();
    const trace = document.querySelector('#osi');
    trace.replaceChildren(...data.osi_trace.map(rawItem => {
      const item = displayOsiItem(rawItem);
      const line = document.createElement('li');
      const layer = document.createElement('b'); layer.textContent = item.layer;
      const explanation = document.createElement('span'); explanation.textContent = item.application;
      line.append(layer, explanation); return line;
    }));
    result.classList.remove('hidden');
    notice.textContent = db.mode === 'DEMO'
      ? `${usingTemporaryDemoPort ? '임시 시연 서버(9091) · ' : ''}DEMO 결과입니다. 실제 DBMS 연결 여부는 확인하지 않았습니다.`
      : 'LIVE 결과입니다. 아래 DBMS 연결 상태와 OSI 통신 추적을 확인하세요.';
  } catch (error) {
    notice.textContent = `오류: ${error.message}`;
    result.classList.add('hidden');
    result.dataset.status = '';
  } finally { button.disabled = false; }
});
