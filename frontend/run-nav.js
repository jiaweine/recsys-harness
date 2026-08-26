(() => {
  const $ = id => document.getElementById(id);
  const reduceMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  let returnFocus = null;
  let selectedIndex = 0;
  let syncQueued = false;

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
        <button type="button" class="run-command-trigger" data-open-command aria-label="打开运行命令面板">导航 <kbd></kbd></button>`;
      head.insertBefore(nav, copy);
    }

    const actions = document.querySelector('.top-actions');
    const inspectorToggle = $('inspectorToggle');
    if (actions && inspectorToggle && !$('runCommandMobile')) {
      const button = document.createElement('button');
      button.id = 'runCommandMobile';
      button.className = 'run-command-mobile';
      button.type = 'button';
      button.hidden = true;
      button.setAttribute('data-open-command', '');
      button.setAttribute('aria-label', '打开运行导航');
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
            <input id="commandInput" type="text" autocomplete="off" spellcheck="false" aria-label="搜索导航命令" placeholder="跳到结果、轨迹或工作区…" />
            <kbd>Esc</kbd>
          </div>
          <div class="command-list" id="commandList" role="listbox" aria-label="导航命令"></div>
          <div class="command-foot"><span id="commandTitle">运行导航</span><b>↑↓ 选择 · Enter 打开</b></div>
        </section>`;
      document.body.appendChild(palette);
    }

    const hint = navigator.platform?.toLowerCase().includes('mac') ? '⌘K' : 'Ctrl K';
    document.querySelectorAll('#runJump kbd').forEach(node => {
      if (node.textContent !== hint) node.textContent = hint;
    });
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
  }

  const commands = [
    {
      id: 'summary', label: '运行概览', detail: 'Run Snapshot · 结论与验证状态', key: '1',
      available: () => resultReady(), run: () => scrollMain('resultSnapshot'),
    },
    {
      id: 'rank', label: '排名结果', detail: 'Rank · Score · 真实排序信号', key: '2',
      available: () => isVisible($('resultAnalysis')) && !!document.querySelector('#resultAnalysis .rank-row'),
      run: () => scrollMain('resultAnalysis'),
    },
    {
      id: 'experiment', label: '策略实验', detail: 'Current / Candidate / Delta · 独立门控', key: '3',
      available: () => isVisible($('strategyExperiment')) && !!document.querySelector('#strategyExperiment .experiment-block'),
      run: () => scrollMain('strategyExperiment'),
    },
    {
      id: 'trace', label: '执行轨迹', detail: 'Mission → Decision → Tool → Reflection → Verify', key: '4',
      available: () => isVisible($('agentTrace')) && !!document.querySelector('#agentTrace .trace-step'),
      run: () => openInspector('progress', 'agentTrace'),
    },
    {
      id: 'evidence', label: '判断依据', detail: 'Verification · Evidence · 可复核来源', key: '5',
      available: () => resultReady(), run: () => openInspector('evidence', 'verificationSummary'),
    },
    {
      id: 'workspace', label: '工作区', detail: '数据集 · 能力状态 · Learning Ledger', key: '6',
      available: () => true, run: () => openInspector('data', 'learningLedger'),
    },
    {
      id: 'input', label: '继续追问', detail: '聚焦任务输入框', key: 'I',
      available: () => true, run: () => $('input')?.focus(),
    },
    {
      id: 'new', label: '新任务', detail: '清空当前上下文并开始新的任务', key: 'N',
      available: () => true, run: () => $('newTaskBtn')?.click(),
    },
  ];

  function commandById(id) {
    return commands.find(command => command.id === id);
  }

  function setActive(id) {
    document.querySelectorAll('#runJump [data-command]').forEach(button => {
      button.classList.toggle('active', button.dataset.command === id);
    });
  }

  function execute(command) {
    if (!command?.available()) return;
    closePalette();
    setActive(command.id);
    command.run();
  }

  function filteredCommands() {
    const query = ($('commandInput')?.value || '').trim().toLowerCase();
    return commands.filter(command => {
      if (!command.available()) return false;
      if (!query) return true;
      return `${command.label} ${command.detail} ${command.id}`.toLowerCase().includes(query);
    });
  }

  function renderCommands() {
    const list = $('commandList');
    if (!list) return;
    const rows = filteredCommands();
    if (!rows.length) {
      selectedIndex = 0;
      list.innerHTML = '<div class="command-empty">没有匹配的导航命令</div>';
      return;
    }
    selectedIndex = Math.max(0, Math.min(selectedIndex, rows.length - 1));
    list.innerHTML = rows.map((command, index) => `
      <button type="button" class="command-item${index === selectedIndex ? ' selected' : ''}" data-palette-command="${command.id}" role="option" aria-selected="${index === selectedIndex}">
        <span class="command-index">${String(index + 1).padStart(2, '0')}</span>
        <span class="command-copy"><b>${command.label}</b><small>${command.detail}</small></span>
        <span class="command-shortcut">${command.key}</span>
      </button>`).join('');
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

  function closePalette() {
    const palette = $('commandPalette');
    if (!palette || palette.hidden) return;
    palette.hidden = true;
    const target = returnFocus;
    returnFocus = null;
    if (target instanceof HTMLElement && document.contains(target)) requestAnimationFrame(() => target.focus());
  }

  function syncUi() {
    syncQueued = false;
    ensureUi();
    const ready = resultReady();
    const nav = $('runJump');
    const mobile = $('runCommandMobile');
    if (nav && nav.hidden === ready) nav.hidden = !ready;
    if (mobile && mobile.hidden === ready) mobile.hidden = !ready;
    document.querySelectorAll('#runJump [data-command]').forEach(button => {
      const command = commandById(button.dataset.command);
      if (!command) return;
      const disabled = !command.available();
      if (button.disabled !== disabled) button.disabled = disabled;
    });
    if (!ready) setActive('');
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
      openPalette(document.activeElement);
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
  observer.observe(document.body, {subtree:true, childList:true, attributes:true, attributeFilter:['hidden']});
  ensureUi();
  syncUi();
})();
