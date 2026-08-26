(() => {
  const $ = id => document.getElementById(id);
  const reduceMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const sceneMeta = {
    search: {short:'搜', label:'搜索', keywords:'搜索 搜 search query ranking'},
    recommend: {short:'荐', label:'推荐', keywords:'推荐 荐 recommend recommendation user'},
    evolve: {short:'优', label:'优化', keywords:'优化 优 evolve evolution experiment'},
    audit: {short:'检', label:'体检', keywords:'体检 检 audit health check'},
  };
  let returnFocus = null;
  let selectedIndex = 0;
  let syncQueued = false;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  function isVisible(node) {
    return !!node && !node.hidden && getComputedStyle(node).display !== 'none';
  }

  function resultReady() {
    return isVisible($('resultSnapshot')) && $('resultSnapshot').textContent.trim().length > 0;
  }

  function ensureUi() {
    const head = document.querySelector('.main-head');
    const copy = $('copyBtn');
    if (head && copy && !$('runJump')) {
      const nav = document.createElement('nav');
      nav.id = 'runJump';
      nav.className = 'run-jump';
      nav.hidden = true;
      nav.setAttribute('aria-label', '运行结果导航');
      nav.innerHTML = `
        <button type="button" data-command="summary">概览</button>
        <button type="button" data-command="rank">排名</button>
        <button type="button" data-command="experiment">实验</button>
        <button type="button" data-command="trace">轨迹</button>
        <button type="button" data-command="evidence">证据</button>
        <button type="button" class="run-command-trigger" data-open-command aria-label="打开工作区导航">导航 <kbd></kbd></button>`;
      head.insertBefore(nav, copy);
    }

    const actions = document.querySelector('.top-actions');
    const inspectorToggle = $('inspectorToggle');
    if (actions && inspectorToggle && !$('runCommandMobile')) {
      const button = document.createElement('button');
      button.id = 'runCommandMobile';
      button.className = 'run-command-mobile';
      button.type = 'button';
      button.hidden = false;
      button.setAttribute('data-open-command', '');
      button.setAttribute('aria-label', '打开工作区导航');
      button.textContent = '导航';
      actions.insertBefore(button, inspectorToggle);
    }

    if (!$('commandPalette')) {
      const palette = document.createElement('div');
      palette.id = 'commandPalette';
      palette.className = 'command-palette';
      palette.hidden = true;
      palette.setAttribute('role', 'presentation');
      palette.innerHTML = `
        <section class="command-dialog" role="dialog" aria-modal="true" aria-labelledby="commandTitle">
          <div class="command-search">
            <span aria-hidden="true">⌘</span>
            <input id="commandInput" type="text" autocomplete="off" spellcheck="false" aria-label="搜索工作区导航" placeholder="搜索运行结果、最近任务或工作区…" />
            <kbd>Esc</kbd>
          </div>
          <div class="command-list" id="commandList" role="listbox" aria-label="工作区导航"></div>
          <div class="command-foot"><span id="commandTitle">工作区导航</span><b>↑↓ 选择 · Enter 打开</b></div>
        </section>`;
      document.body.appendChild(palette);
    }

    const hint = navigator.platform?.toLowerCase().includes('mac') ? '⌘K' : 'Ctrl K';
    document.querySelectorAll('#runJump kbd').forEach(node => {
      if (node.textContent !== hint) node.textContent = hint;
    });
  }

  function focusSection(node) {
    if (!(node instanceof HTMLElement)) return;
    if (!node.hasAttribute('tabindex')) node.setAttribute('tabindex', '-1');
    node.setAttribute('data-run-nav-focus', '');
    node.focus({preventScroll:true});
  }

  function inspectorOnScreen() {
    const inspector = $('inspector');
    if (!inspector) return false;
    const style = getComputedStyle(inspector);
    if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
    const rect = inspector.getBoundingClientRect();
    return rect.width > 80 && rect.right > 8 && rect.left < window.innerWidth - 8;
  }

  function openInspector(tabName, targetId) {
    const inspector = $('inspector');
    if (!inspector) return;
    if (!inspectorOnScreen()) $('inspectorToggle')?.click();
    const tab = document.querySelector(`.tab[data-tab="${tabName}"]`);
    tab?.click();
    tab?.focus({preventScroll:true});
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const target = targetId ? $(targetId) : null;
      if (target && isVisible(target)) {
        target.scrollIntoView({behavior: reduceMotion() ? 'auto' : 'smooth', block: 'start'});
      } else {
        inspector.querySelector('.inspector-body')?.scrollTo({top: 0, behavior: reduceMotion() ? 'auto' : 'smooth'});
      }
    }));
  }

  function scrollMain(id) {
    const area = $('scrollArea');
    const node = $(id);
    if (!area || !node || !isVisible(node)) return;
    const areaRect = area.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const nextTop = area.scrollTop + nodeRect.top - areaRect.top - 18;
    area.scrollTo({top: Math.max(0, nextTop), behavior: reduceMotion() ? 'auto' : 'smooth'});
    requestAnimationFrame(() => focusSection(node));
  }

  const commands = [
    {
      id: 'summary', group: 'navigate', label: '运行概览', detail: 'Run Snapshot · 结论与验证状态', key: '1',
      available: () => resultReady(), run: () => scrollMain('resultSnapshot'),
    },
    {
      id: 'rank', group: 'navigate', label: '排名结果', detail: 'Rank · Score · 真实排序信号', key: '2',
      available: () => isVisible($('resultAnalysis')) && !!document.querySelector('#resultAnalysis .rank-row'),
      run: () => scrollMain('resultAnalysis'),
    },
    {
      id: 'experiment', group: 'navigate', label: '策略实验', detail: 'Current / Candidate / Delta · 独立门控', key: '3',
      available: () => isVisible($('strategyExperiment')) && !!document.querySelector('#strategyExperiment .experiment-block'),
      run: () => scrollMain('strategyExperiment'),
    },
    {
      id: 'trace', group: 'navigate', label: '执行轨迹', detail: 'Mission → Decision → Tool → Reflection → Verify', key: '4',
      available: () => isVisible($('agentTrace')) && !!document.querySelector('#agentTrace .trace-step'),
      run: () => openInspector('progress', 'agentTrace'),
    },
    {
      id: 'evidence', group: 'navigate', label: '判断依据', detail: 'Verification · Evidence · 可复核证据与来源', key: '5',
      available: () => resultReady(), run: () => openInspector('evidence', 'verificationSummary'),
    },
    {
      id: 'workspace', group: 'actions', label: '工作区', detail: '数据集 · 能力状态 · Learning Ledger', key: '6',
      available: () => true, run: () => openInspector('data', 'learningLedger'),
    },
    {
      id: 'input', group: 'actions', label: '继续追问', detail: '聚焦任务输入框', key: 'I',
      available: () => true, run: () => $('input')?.focus(),
    },
    {
      id: 'new', group: 'actions', label: '新任务', detail: '清空当前上下文并开始新的任务', key: 'N',
      available: () => true,
      run: () => {
        const button = $('newTaskBtn');
        button?.click();
        button?.focus({preventScroll:true});
      },
    },
  ];

  function historyCommands() {
    return [...document.querySelectorAll('#historyList .history-item[data-id]')].map(button => {
      const id = button.dataset.id || '';
      const scene = button.dataset.scene || '';
      const meta = sceneMeta[scene] || {short:'·', label:'任务', keywords:'任务 history run'};
      const title = button.querySelector('b')?.textContent.trim() || '历史任务';
      const time = button.querySelector('small')?.textContent.trim() || '';
      const running = button.classList.contains('running');
      const current = button.getAttribute('aria-current') === 'page';
      const state = running ? '运行中' : current ? '当前' : '';
      return {
        id: `history:${id}`,
        group: 'history',
        kind: 'history',
        historyId: id,
        label: title,
        detail: [meta.label, time].filter(Boolean).join(' · '),
        key: state,
        index: meta.short,
        current,
        running,
        search: `${title} ${meta.label} ${meta.keywords} ${time} ${state} history run`,
        available: () => !!id,
        run: () => {
          const live = [...document.querySelectorAll('#historyList .history-item[data-id]')].find(node => node.dataset.id === id);
          live?.click();
          requestAnimationFrame(() => live?.focus({preventScroll:true}));
        },
      };
    });
  }

  function allCommands() {
    return [...commands.filter(command => command.available()), ...historyCommands()];
  }

  function commandById(id) {
    return commands.find(command => command.id === id) || historyCommands().find(command => command.id === id);
  }

  function setActive(id) {
    document.querySelectorAll('#runJump [data-command]').forEach(button => {
      button.classList.toggle('active', button.dataset.command === id);
    });
  }

  function execute(command) {
    if (!command?.available()) return;
    closePalette(false);
    if (command.kind !== 'history') setActive(command.id);
    command.run();
  }

  function filteredCommands() {
    const query = ($('commandInput')?.value || '').trim().toLowerCase();
    return allCommands().filter(command => {
      if (!query) return true;
      const haystack = command.search || `${command.label} ${command.detail} ${command.id}`;
      return haystack.toLowerCase().includes(query);
    });
  }

  function groupLabel(group) {
    if (group === 'navigate') return '当前运行';
    if (group === 'actions') return '工作区';
    return '最近任务';
  }

  function renderCommands() {
    const list = $('commandList');
    if (!list) return;
    const rows = filteredCommands();
    if (!rows.length) {
      selectedIndex = 0;
      list.innerHTML = '<div class="command-empty">没有匹配的运行结果或任务</div>';
      return;
    }
    selectedIndex = Math.max(0, Math.min(selectedIndex, rows.length - 1));
    let lastGroup = '';
    const html = [];
    rows.forEach((command, index) => {
      if (command.group !== lastGroup) {
        html.push(`<div class="command-group" role="presentation">${escapeHtml(groupLabel(command.group))}</div>`);
        lastGroup = command.group;
      }
      const classes = ['command-item'];
      if (index === selectedIndex) classes.push('selected');
      if (command.kind === 'history') classes.push('history-command');
      if (command.current) classes.push('current');
      if (command.running) classes.push('live');
      const marker = command.index || String(index + 1).padStart(2, '0');
      html.push(`
        <button type="button" class="${classes.join(' ')}" data-palette-command="${escapeHtml(command.id)}"${command.kind === 'history' ? ` data-command-kind="history" data-history-id="${escapeHtml(command.historyId)}"` : ''} role="option" aria-selected="${index === selectedIndex}">
          <span class="command-index">${escapeHtml(marker)}</span>
          <span class="command-copy"><b>${escapeHtml(command.label)}</b><small>${escapeHtml(command.detail)}</small></span>
          <span class="command-shortcut">${escapeHtml(command.key || '')}</span>
        </button>`);
    });
    list.innerHTML = html.join('');
    list.querySelector('.command-item.selected')?.scrollIntoView({block: 'nearest'});
  }

  function openPalette(source) {
    ensureUi();
    const palette = $('commandPalette');
    if (!palette) return;
    returnFocus = source instanceof HTMLElement ? source : document.activeElement;
    palette.hidden = false;
    selectedIndex = 0;
    const input = $('commandInput');
    if (input) input.value = '';
    renderCommands();
    requestAnimationFrame(() => input?.focus());
  }

  function closePalette(restoreFocus = true) {
    const palette = $('commandPalette');
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    const target = returnFocus;
    returnFocus = null;
    if (restoreFocus && target instanceof HTMLElement && document.contains(target)) {
      requestAnimationFrame(() => target.focus());
    }
  }

  function syncUi() {
    syncQueued = false;
    ensureUi();
    const ready = resultReady();
    const nav = $('runJump');
    const mobile = $('runCommandMobile');
    if (nav && nav.hidden === ready) nav.hidden = !ready;
    if (mobile) mobile.hidden = false;
    document.querySelectorAll('#runJump [data-command]').forEach(button => {
      const command = commands.find(item => item.id === button.dataset.command);
      if (!command) return;
      const disabled = !command.available();
      if (button.disabled !== disabled) button.disabled = disabled;
    });
    if (!ready) setActive('');
    else if (!document.querySelector('#runJump [data-command].active')) setActive('summary');
    const palette = $('commandPalette');
    if (palette && !palette.hidden) renderCommands();
  }

  function scheduleSync() {
    if (syncQueued) return;
    syncQueued = true;
    requestAnimationFrame(syncUi);
  }

  document.addEventListener('click', event => {
    const open = event.target.closest('[data-open-command]');
    if (open) {
      openPalette(open);
      return;
    }
    const jump = event.target.closest('#runJump [data-command]');
    if (jump) {
      execute(commandById(jump.dataset.command));
      return;
    }
    const item = event.target.closest('[data-palette-command]');
    if (item) {
      execute(commandById(item.dataset.paletteCommand));
      return;
    }
    if (event.target === $('commandPalette')) closePalette();
    const tab = event.target.closest('.tab[data-tab]');
    if (tab?.dataset.tab === 'progress') setActive('trace');
    if (tab?.dataset.tab === 'evidence') setActive('evidence');
    if (tab?.dataset.tab === 'data') setActive('workspace');
    if (event.target.closest('#newTaskBtn, .scene, .history-item')) scheduleSync();
  });

  document.addEventListener('input', event => {
    if (event.target === $('commandInput')) {
      selectedIndex = 0;
      renderCommands();
    }
  });

  document.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      const palette = $('commandPalette');
      if (palette && !palette.hidden) closePalette();
      else openPalette(document.activeElement);
      return;
    }
    const palette = $('commandPalette');
    if (!palette || palette.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closePalette();
      return;
    }
    const rows = filteredCommands();
    if (!rows.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      selectedIndex = (selectedIndex + 1) % rows.length;
      renderCommands();
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      selectedIndex = (selectedIndex - 1 + rows.length) % rows.length;
      renderCommands();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      execute(rows[selectedIndex]);
    }
  });

  const observer = new MutationObserver(scheduleSync);
  observer.observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden','aria-current','class']});
  ensureUi();
  syncUi();
})();
