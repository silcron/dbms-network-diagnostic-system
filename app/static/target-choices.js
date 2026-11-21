function renderTargetChoices(targets, primary, summary) {
  const container = document.querySelector('#targets');
  container.replaceChildren();
  document.querySelector('#targets-panel').hidden = targets.length < 2;
  setText('#feedback-summary', targets.length > 1
    ? `전체 상태 ${primary.status}${summary ? ` · ${summary}` : ''}`
    : (summary || ''));
  result.dataset.overallStatus = primary.status.toLowerCase();
  renderTargetDetail(primary);
  if (targets.length < 2) return;

  targets.forEach(db => {
    const choice = document.createElement('button');
    choice.type = 'button';
    choice.className = 'target-choice';
    choice.dataset.status = db.status;
    choice.setAttribute('aria-controls', 'target-detail');
    choice.setAttribute('aria-pressed', String(db.target_id === primary.target_id));
    const name = document.createElement('strong');
    name.textContent = `DB ${db.target_id}`;
    const status = document.createElement('b');
    status.textContent = db.status;
    const endpoint = document.createElement('small');
    endpoint.textContent = db.database;
    const finding = document.createElement('span');
    finding.className = 'target-finding';
    finding.textContent = db.finding || db.detail;
    choice.append(name, status, finding, endpoint);
    choice.addEventListener('click', () => {
      for (const item of container.children) {
        item.setAttribute('aria-pressed', String(item === choice));
      }
      renderTargetDetail(db);
    });
    container.append(choice);
  });
}
