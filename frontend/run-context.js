(() => {
  const $ = id => document.getElementById(id);
  const sceneMeta = {
    search: {short:'搜', label:'搜索'},
    recommend: {short:'荐', label:'推荐'},
    evolve: {short:'优', label:'优化'},
    audit: {short:'检', label:'体检'},
  };
  let queued = false;
  let signature = '';

  function ensureStrip() {
    let strip = $('runContextStrip');
    if (strip) return strip;
    const head = document.querySelector('.main-head');
    if (!head) return null;
    strip = document.createElement('section');
    strip.id = 'runContextStrip';
    strip.className = 'run-context-strip';
    strip.setAttribute('aria-label', '当前运行上下文');
    head.insertAdjacentElement('afterend', strip);
    return strip;
  }

  function currentHistory() {
    return document.querySelector('#historyList .history-item[data-id][aria-current="page"]');
  }

  function currentScene(history) {
    const fromHistory = history?.dataset.scene;
    if (fromHistory && sceneMeta[fromHistory]) return fromHistory;
    const active = document.querySelector('.scene.active[data-scene]')?.dataset.scene;
    return sceneMeta[active] ? active : 'audit';
  }

  function resultTargets() {
    const analysis = $('resultAnalysis');
    if (!analysis || analysis.hidden) return [];
    const values = [...analysis.querySelectorAll('.analysis-head > b')]
      .map(node => node.textContent.trim())
      .filter(Boolean);
    return [...new Set(values)].slice(0, 2);
  }

  function contextState() {
    const interaction = $('taskState')?.textContent.trim() || '等待输入';
    const execution = $('stateText')?.textContent.trim() || '';
    if (/连接中断|正在重连|停止中/.test(interaction)) return interaction;
    if (execution && execution !== '等待开始') return execution;
    return interaction;
  }

  function stateTone(value) {
    const text = String(value || '');
    if (/正在|重连|停止中/.test(text)) return 'live';
    if (/失败|需要|中断|已停止/.test(text)) return 'warn';
    return 'neutral';
  }

  function verificationState() {
    const snapshot = $('resultSnapshot');
    const state = snapshot?.querySelector('.snapshot-state');
    if (!snapshot || snapshot.hidden || !state) return null;
    if (state.classList.contains('pass')) return {label:'验证通过', tone:'pass'};
    if (state.classList.contains('review')) return {label:'待复核', tone:'review'};
    const label = state.textContent.trim();
    return label ? {label, tone:'neutral'} : null;
  }

  function item(className, key, value, options = {}) {
    const node = document.createElement('span');
    node.className = `run-context-item ${className}`;
    if (options.tone) node.dataset.tone = options.tone;
    if (options.dot) {
      const dot = document.createElement('i');
      dot.className = 'run-context-dot';
      dot.setAttribute('aria-hidden', 'true');
      node.appendChild(dot);
    }
    if (options.badge) {
      const badge = document.createElement('strong');
      badge.textContent = options.badge;
      badge.setAttribute('aria-hidden', 'true');
      node.appendChild(badge);
    }
    if (key) {
      const keyNode = document.createElement('i');
      keyNode.className = 'run-context-key';
      keyNode.textContent = key;
      node.appendChild(keyNode);
    }
    const valueNode = document.createElement('b');
    valueNode.className = 'run-context-value';
    valueNode.textContent = value;
    node.appendChild(valueNode);
    if (options.title) node.title = options.title;
    return node;
  }

  function render() {
    queued = false;
    const strip = ensureStrip();
    if (!strip) return;

    const history = currentHistory();
    const scene = currentScene(history);
    const meta = sceneMeta[scene];
    const taskTitle = $('taskTitle')?.textContent.trim() || '新的体验任务';
    const taskState = contextState();
    const when = history?.querySelector('small')?.textContent.trim() || '';
    const targets = resultTargets();
    const verification = verificationState();
    const currentSignature = JSON.stringify({scene, taskTitle, taskState, when, targets, verification});
    if (currentSignature === signature) return;
    signature = currentSignature;

    const nodes = [];
    nodes.push(item('run-context-scene', '', meta.label, {badge:meta.short, title:`${meta.label}任务`}));
    nodes.push(item('run-context-mobile-title', '任务', taskTitle, {title:taskTitle}));

    if (targets.length) {
      const key = scene === 'search' ? '查询' : scene === 'recommend' ? '用户' : '目标';
      nodes.push(item('run-context-target', key, targets.join(' / '), {title:targets.join(' / ')}));
    }

    nodes.push(item('run-context-state', '', taskState, {dot:true, tone:stateTone(taskState)}));
    if (when) nodes.push(item('run-context-time', '更新', when));
    if (verification) nodes.push(item('run-context-verification', '', verification.label, {dot:true, tone:verification.tone}));

    strip.replaceChildren(...nodes);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(render);
  }

  document.addEventListener('click', event => {
    if (event.target.closest('#newTaskBtn, .scene, .history-item, #sendBtn, [data-open-command], [data-palette-command]')) {
      schedule();
    }
  }, true);

  const observer = new MutationObserver(mutations => {
    if (mutations.every(mutation => mutation.target.closest?.('#runContextStrip'))) return;
    schedule();
  });
  observer.observe(document.body, {
    subtree:true,
    childList:true,
    characterData:true,
    attributes:true,
    attributeFilter:['hidden','class','aria-current','data-scene'],
  });

  ensureStrip();
  render();
})();
