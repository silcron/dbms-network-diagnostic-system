// Run with: node --test tests/test_target_selection.cjs
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page() {
  class Element {
    constructor() {
      this.children = []; this.dataset = {}; this.attrs = {}; this.events = {};
      this.textContent = ''; this.value = ''; this.hidden = false; this.disabled = false;
      const values = new Set();
      this.classList = {
        add: (...xs) => xs.forEach(x => values.add(x)),
        remove: (...xs) => xs.forEach(x => values.delete(x)),
        contains: x => values.has(x),
        toggle: (x, on) => { on = on === undefined ? !values.has(x) : on; on ? values.add(x) : values.delete(x); return on; },
      };
    }
    replaceChildren(...xs) { this.children = xs; }
    append(...xs) { this.children.push(...xs); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    removeAttribute(k) { delete this.attrs[k]; }
    addEventListener(k, fn) { this.events[k] = fn; }
    click() { this.events.click?.(); }
    querySelector() { return new Element(); }
  }
  const nodes = new Map();
  const get = selector => { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); };
  get('input[name="mode"]:checked').value = 'demo';
  get('#host').value = '127.0.0.1'; get('#port').value = '62036';
  const context = vm.createContext({
    document: { querySelector: get, querySelectorAll: () => [], createElement: () => new Element() },
    console, Date, Number, String, Error, JSON, setTimeout,
  });
  for (const name of ['target-choices.js', 'app.js']) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../app/static', name), 'utf8'), context);
  }
  function render(targets) {
    context.payload = { results: targets, result: targets.at(-1), client_rtt_ms: .5,
      checked_at:'2026-11-21T12:00:00+09:00', feedback_summary:'정상 1 · 경고 1 · 장애 1',
      osi_trace:[{layer:'4 전송 계층', application:'TCP'}] };
    vm.runInContext('renderDatabaseResult(payload)', context);
  }
  return { get, render, context };
}

const targets = ['NORMAL','WARNING','DOWN'].map((status, i) => ({
  target_id:i+1, status, mode:'DEMO', database:`DB ${i+1}`, detail:`detail ${status}`,
  scenario:['normal','warning','down'][i], total_ms:i+1, next_check:`check ${status}`,
}));

test('card selection updates the headline, color, details and selected button', () => {
  const p = page(); p.render(targets);
  for (const index of [0,1,2,0]) {
    const cards = p.get('#targets').children;
    cards[index].click();
    assert.equal(p.get('#status').textContent, targets[index].status);
    assert.equal(p.get('#status-label').textContent, `DB ${index+1} · 선택한 DB 상태`);
    assert.equal(p.get('#result').dataset.status, targets[index].status.toLowerCase());
    assert.equal(p.get('#detail').textContent, targets[index].detail);
    assert.equal(p.get('#route').classList.contains('is-db-error'), index === 2);
    cards.forEach((card,i) => assert.equal(card.attrs['aria-pressed'], String(i === index)));
  }
});

test('selection retains the batch status, response timestamp and request duration', () => {
  const p = page(); p.render(targets);
  const summary = p.get('#feedback-summary').textContent;
  assert.match(summary, /전체 상태 DOWN/);
  for (const card of p.get('#targets').children) {
    card.click();
    assert.equal(p.get('#feedback-summary').textContent, summary);
    assert.equal(p.get('#result').dataset.overallStatus, 'down');
    assert.equal(p.get('#rtt').textContent, '0.5 ms');
    assert.equal(p.get('#checked').textContent, '2026.11.21 12:00:00');
  }
});

test('single and legacy results update the headline without stale target cards', () => {
  const p = page(); p.render(targets);
  p.render([{status:'NORMAL', mode:'DEMO', database:'single', detail:'normal'}]);
  assert.equal(p.get('#status').textContent, 'NORMAL');
  assert.equal(p.get('#status-label').textContent, '진단 상태');
  assert.equal(p.get('#targets-panel').hidden, true);
  assert.equal(p.get('#targets').children.length, 0);
  assert.equal(p.get('#route').classList.contains('is-db-error'), false);
});
