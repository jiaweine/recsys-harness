(() => {
  const history = document.getElementById('historyList');
  if (!history) return;

  const sceneMeta = {
    search: {short:'搜', label:'搜索'},
    recommend: {short:'荐', label:'推荐'},
    evolve: {short:'优', label:'优化'},
    audit: {short:'检', label:'体检'},
  };
  let currentId = null;
  let requestToken = 0;
  let hydrateQueued = false;
  let firstPaint = true;
  let selectFirstOnNextRender = false;

  function xhrRows() {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('GET', '/api/conversations', true);
      request.responseType = 'json';
      request.withCredentials = true;
      request.timeout = 8000;
      request.onload = () => {
        if (request.status >= 200 && request.status < 300) {
          resolve(Array.isArray(request.response) ? request.response : []);
          return;
        }
        reject(new Error(`history metadata ${request.status}`));
      };
      request.onerror = () => reject(new Error('history metadata unavailable'));
      request.ontimeout = () => reject(new Error('history metadata timeout'));
      request.send();
    });
  }

  function stateLabel(button, running, current) {
    let node = button.querySelector('.history-state');
    const label = running ? '运行中' : current ? '当前' : '';
    if (!label) {
      node?.remove();
      return;
    }
    if (!node) {
      node = document.createElement('span');
      node.className = 'history-state';
      button.appendChild(node);
    }
    node.textContent = label;
    node.classList.toggle('live', running);
  }

  function decorate(button, row) {
    const meta = sceneMeta[row?.scene] || {short:'·', label:'任务'};
    const running = button.classList.contains('running') || row?.active === true;
    const current = !!currentId && button.dataset.id === currentId;

    button.classList.toggle('current', current);
    button.setAttribute('aria-current', current ? 'page' : 'false');
    button.dataset.scene = row?.scene || '';

    let badge = button.querySelector('.history-scene');
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'history-scene';
      badge.setAttribute('aria-hidden', 'true');
      button.prepend(badge);
    }
    badge.textContent = meta.short;

    const time = button.querySelector('small');
    if (time) time.setAttribute('aria-label', `${meta.label}任务 · ${time.textContent.trim()}`);
    stateLabel(button, running, current);

    const title = button.querySelector('b')?.textContent.trim() || '任务';
    const when = time?.textContent.trim() || '';
    button.title = `${title} · ${meta.label}${when ? ` · ${when}` : ''}${running ? ' · 运行中' : current ? ' · 当前' : ''}`;
  }

  async function hydrate() {
    hydrateQueued = false;
    const buttons = [...history.querySelectorAll('.history-item[data-id]')];
    if (!buttons.length) return;
    if ((firstPaint || selectFirstOnNextRender) && !currentId) currentId = buttons[0].dataset.id || null;
    firstPaint = false;
    selectFirstOnNextRender = false;

    const token = ++requestToken;
    try {
      const rows = await xhrRows();
      if (token !== requestToken) return;
      const byId = new Map(rows.map(row => [String(row.id), row]));
      buttons.forEach(button => decorate(button, byId.get(button.dataset.id) || {}));
    } catch {
      if (token !== requestToken) return;
      buttons.forEach(button => decorate(button, {}));
    }
  }

  function scheduleHydrate() {
    if (hydrateQueued) return;
    hydrateQueued = true;
    requestAnimationFrame(() => requestAnimationFrame(hydrate));
  }

  document.addEventListener('click', event => {
    const item = event.target.closest('.history-item[data-id]');
    if (item) {
      currentId = item.dataset.id || null;
      scheduleHydrate();
      return;
    }
    if (event.target.closest('#newTaskBtn, .scene')) {
      currentId = null;
      requestToken += 1;
      scheduleHydrate();
      return;
    }
    if (event.target.closest('#sendBtn') && !currentId) selectFirstOnNextRender = true;
  }, true);

  document.addEventListener('keydown', event => {
    if (event.target === document.getElementById('input') && event.key === 'Enter' && !event.shiftKey && !event.isComposing && !currentId) {
      selectFirstOnNextRender = true;
    }
  }, true);

  const observer = new MutationObserver(scheduleHydrate);
  observer.observe(history, {childList:true});
  scheduleHydrate();
})();
