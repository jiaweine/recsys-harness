(() => {
  const upstreamFetch = window.fetch.bind(window);
  let lastResult = null;

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[char]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : 0;

  function ensureMounts() {
    const progress = $('panel-progress');
    const stateRow = progress?.querySelector('.state-row');
    const telemetry = $('runTelemetry');
    if (progress && stateRow && telemetry && !$('runControlPlane')) {
      const node = document.createElement('section');
      node.id = 'runControlPlane';
      node.className = 'run-control-plane';
      node.hidden = true;
      progress.insertBefore(node, telemetry);
    }

    const dataPanel = $('panel-data');
    const importLine = $('importBtnSide');
    if (dataPanel && importLine && !$('learningLedger')) {
      const node = document.createElement('section');
      node.id = 'learningLedger';
      node.className = 'learning-ledger';
      node.hidden = true;
      dataPanel.insertBefore(node, importLine);
    }
  }

  function level(value, max) {
    const denominator = Math.max(0.0001, num(max));
    return Math.max(0, Math.min(10, Math.round((num(value) / denominator) * 10)));
  }

  function setTabCount(name, value) {
    const tab = document.querySelector(`.tab[data-tab="${name}"]`);
    if (!tab) return;
    let badge = tab.querySelector('.tab-count');
    const count = Math.max(0, Number(value) || 0);
    if (!count) {
      if (badge) badge.remove();
      return;
    }
    if (!badge) {
      badge = document.createElement('span');
      badge.className = 'tab-count';
      badge.setAttribute('aria-hidden', 'true');
      tab.appendChild(badge);
    }
    badge.textContent = count > 99 ? '99+' : String(count);
  }

  function syncTabCounts(result = null, events = null) {
    const rows = events || result?.events || [];
    setTabCount('progress', Array.isArray(rows) ? rows.length : 0);
    setTabCount('evidence', Array.isArray(result?.evidence) ? result.evidence.length : 0);
  }

  function permissionCell(label, allowed, detail) {
    const known = typeof allowed === 'boolean';
    const tone = !known ? 'pending' : allowed ? 'allowed' : 'locked';
    const state = !known ? '待确认' : allowed ? '已授权' : '锁定';
    return `<div class="control-cell ${tone}">
      <span>${esc(label)}</span>
      <b><i></i>${state}</b>
      <small>${esc(detail)}</small>
    </div>`;
  }

  function budgetCell(label, used, max) {
    const hasMax = Number.isFinite(Number(max)) && Number(max) > 0;
    const current = num(used);
    const maxValue = hasMax ? num(max) : null;
    const decimals = label.includes('成本') ? 1 : 0;
    const value = hasMax ? `${current.toFixed(decimals)} / ${maxValue}` : current.toFixed(decimals);
    return `<div class="control-cell budget">
      <span>${esc(label)}</span>
      <b>${esc(value)}</b>
      <em class="budget-bar" data-level="${hasMax ? level(current, maxValue) : 0}"><i></i></em>
    </div>`;
  }

  function liveControlHtml(events, status) {
    const rows = Array.isArray(events) ? events : [];
    const guard = [...rows].reverse().find(event => event.phase === 'guard')?.payload || null;
    const executes = rows.filter(event => event.phase === 'execute');
    const decisions = rows.filter(event => event.phase === 'decide');
    const cost = executes.reduce((sum, event) => sum + num(event.payload?.cost), 0);
    const cycles = Math.max(0, ...decisions.map(event => num(event.payload?.cycle)));
    const adaptationPermission = guard ? !!guard.allow_adaptation : null;
    const networkPermission = guard ? !!guard.allow_network : null;

    return `
      <div class="control-head">
        <div><span>CONTROL PLANE</span><h3>自主执行边界</h3></div>
        <strong class="control-run-state"><i></i>${status === 'cancel_requested' ? '停止中' : 'RUNNING'}</strong>
      </div>
      <div class="control-grid">
        ${permissionCell('策略变化', adaptationPermission, guard ? '仅在本任务授权范围内' : '等待运行边界事件确认')}
        ${permissionCell('联网研究', networkPermission, guard ? '仅使用公开来源' : '等待运行边界事件确认')}
        ${budgetCell('已用成本', cost, null)}
        ${budgetCell('执行轮次', cycles, null)}
      </div>`;
  }

  function completedControlHtml(result) {
    const plan = result.plan || {};
    const autonomy = result.autonomy || {};
    const budget = autonomy.budget || {};
    const constraints = Array.isArray(plan.constraints) ? plan.constraints.slice(0, 5) : [];
    const activated = (result.actions || []).some(action => action.result?.activated === true);
    const networkUsed = result.network?.used === true;
    const passed = result.verification?.passed === true;

    return `
      <div class="control-head">
        <div><span>CONTROL PLANE</span><h3>自主执行边界</h3></div>
        <strong class="control-run-state done"><i></i>${passed ? 'VERIFIED' : 'DONE'}</strong>
      </div>
      <div class="control-grid">
        ${permissionCell('策略变化', !!plan.allow_adaptation, activated ? '本轮已发生经验证的策略变化' : plan.allow_adaptation ? '已授权，但未自动改变当前策略' : '本轮禁止改变当前策略')}
        ${permissionCell('联网研究', !!plan.allow_network, networkUsed ? '本轮实际使用了公开资料' : plan.allow_network ? '已授权，但本轮未使用' : '本轮未授权联网')}
        ${budgetCell('执行成本', autonomy.spent_cost, budget.max_cost)}
        ${budgetCell('工具预算', autonomy.cycles, budget.max_tools)}
      </div>
      <div class="control-foot">
        <span>时间上限 <b>${num(budget.max_seconds)}s</b></span>
        <span>Memory <b>${num(autonomy.memory_hits)} hits</b></span>
        <span>约束遵循 <b>${autonomy.constraints_respected === false ? '需复核' : '通过'}</b></span>
      </div>
      ${constraints.length ? `<div class="control-constraints"><span>任务约束</span><div>${constraints.map(item => `<i>${esc(item)}</i>`).join('')}</div></div>` : ''}`;
  }

  function safetyChip(label, enabled, warning = false) {
    const tone = warning ? 'warn' : enabled ? 'pass' : 'quiet';
    return `<span class="safety-chip ${tone}"><i></i>${esc(label)}</span>`;
  }

  function learningHtml(result) {
    const evolution = result.evolution || {};
    const memory = evolution.memory || {};
    const learned = Array.isArray(evolution.learned) ? evolution.learned : [];
    const dataInspect = [...(result.actions || [])].reverse().find(action => action.tool === 'data.inspect' && action.status === 'completed');
    const rollbacks = Array.isArray(dataInspect?.result?.rollbacks) ? dataInspect.result.rollbacks : [];
    const durability = result.durability || {};

    return `
      <div class="learning-head">
        <div><span>LEARNING LEDGER</span><h3>策略经验与恢复</h3></div>
        <strong>${learned.length ? `+${learned.length}` : '0'} 本轮</strong>
      </div>
      <div class="learning-stats">
        <span><b>${num(memory.episodes)}</b><i>执行记忆</i></span>
        <span><b>${num(memory.skills)}</b><i>可信策略</i></span>
        <span><b>${num(memory.active_strategies)}</b><i>当前启用</i></span>
      </div>
      <div class="safety-row">
        ${safetyChip('独立评估门', evolution.eval_gated !== false)}
        ${safetyChip('自动回滚', evolution.automatic_rollback !== false, rollbacks.length > 0)}
        ${safetyChip('断点恢复', durability.checkpoint_resume !== false)}
        ${safetyChip('动作幂等', durability.idempotent_adaptive_tools !== false)}
      </div>
      ${rollbacks.length ? `<div class="rollback-note"><span>ROLLBACK</span><b>检测到 ${rollbacks.length} 次历史策略回退，已恢复稳健策略</b></div>` : ''}
      <div class="learning-foot">
        <span>本次执行${durability.resumed ? '从断点恢复后继续' : '从新任务状态开始'}</span>
        <b>${learned.length ? `${learned.length} 条经验通过学习门槛` : '没有未经验证的策略写入'}</b>
      </div>`;
  }

  function syncLiveTraceSummary(events) {
    const trace = $('agentTrace');
    const cells = trace ? trace.querySelectorAll('.trace-overview > span') : [];
    if (cells.length >= 3) {
      const launched = (events || []).filter(event => event.phase === 'execute').length;
      const value = cells[2].querySelector('b');
      const label = cells[2].querySelector('i');
      if (value) value.textContent = String(launched);
      if (label) label.textContent = '动作已发起';
    }
    const latest = (events || []).at(-1);
    const bar = $('runBar');
    if (bar && latest) bar.dataset.level = String(level(latest.progress, 100));
  }

  function normalizeMissionLabels() {
    const labels = {
      dismissed: '已排除',
      resolved: '已解决',
      supported: '被支持',
      open: '观察中',
    };
    document.querySelectorAll('#missionSummary .hypothesis-row small').forEach(node => {
      const key = node.textContent.trim();
      if (labels[key]) node.textContent = labels[key];
    });
  }

  function normalizeTraceKeys(result) {
    const mission = result?.deliberation?.mission || {};
    const keyLabels = {};
    Object.entries(mission.requirements || {}).forEach(([key, row]) => {
      keyLabels[key] = row?.label || key;
    });
    Object.entries(mission.hypotheses || {}).forEach(([key, row]) => {
      keyLabels[key] = row?.label || key;
    });
    if (!Object.keys(keyLabels).length) return;
    document.querySelectorAll('#agentTrace .trace-facts b, #agentTrace .trace-chips span').forEach(node => {
      const key = node.textContent.trim();
      if (keyLabels[key]) node.textContent = keyLabels[key];
    });
  }

  function renderLive(events, status) {
    ensureMounts();
    const node = $('runControlPlane');
    if (!node || !Array.isArray(events) || !events.length) return;
    node.innerHTML = liveControlHtml(events, status);
    node.hidden = false;
    syncTabCounts(null, events);
    requestAnimationFrame(() => {
      syncLiveTraceSummary(events);
      normalizeMissionLabels();
    });
  }

  function renderCompleted(result) {
    if (!result || typeof result !== 'object') return;
    lastResult = result;
    ensureMounts();
    const control = $('runControlPlane');
    const ledger = $('learningLedger');
    if (control) {
      control.innerHTML = completedControlHtml(result);
      control.hidden = false;
    }
    if (ledger) {
      ledger.innerHTML = learningHtml(result);
      ledger.hidden = false;
    }
    syncTabCounts(result);
    const bar = $('runBar');
    if (bar) bar.dataset.level = '10';
    requestAnimationFrame(() => {
      normalizeMissionLabels();
      normalizeTraceKeys(result);
    });
  }

  function clear() {
    lastResult = null;
    ['runControlPlane', 'learningLedger'].forEach(id => {
      const node = $(id);
      if (node) {
        node.hidden = true;
        node.innerHTML = '';
      }
    });
    document.querySelectorAll('.tab-count').forEach(node => node.remove());
    const bar = $('runBar');
    if (bar) delete bar.dataset.level;
  }

  function inspectPayload(path, payload) {
    if (!payload || typeof payload !== 'object') return;
    if (path.startsWith('/api/runs/')) {
      if (payload.status === 'completed' && payload.result) requestAnimationFrame(() => renderCompleted(payload.result));
      else if (Array.isArray(payload.events)) requestAnimationFrame(() => renderLive(payload.events, payload.status || 'running'));
      return;
    }
    if (/^\/api\/conversations\/[^/]+$/.test(path) && Array.isArray(payload.messages)) {
      const assistant = [...payload.messages].reverse().find(message => message.role === 'assistant' && message.payload);
      if (assistant?.payload) requestAnimationFrame(() => renderCompleted(assistant.payload));
    }
  }

  window.fetch = async (...args) => {
    const response = await upstreamFetch(...args);
    try {
      const input = args[0];
      const rawUrl = typeof input === 'string' ? input : input?.url;
      const path = new URL(rawUrl, location.origin).pathname;
      if (response.ok && (path.startsWith('/api/runs/') || /^\/api\/conversations\/[^/]+$/.test(path))) {
        response.clone().json().then(payload => inspectPayload(path, payload)).catch(() => {});
      }
    } catch (_) {}
    return response;
  };

  document.addEventListener('click', event => {
    if (event.target.closest('#newTaskBtn, .scene, .history-item')) clear();
  });

  ensureMounts();
  const observer = new MutationObserver(() => {
    ensureMounts();
    normalizeMissionLabels();
    if (lastResult) {
      normalizeTraceKeys(lastResult);
      syncTabCounts(lastResult);
    }
    if (lastResult && ($('runControlPlane')?.hidden || $('learningLedger')?.hidden)) renderCompleted(lastResult);
  });
  observer.observe(document.body, {subtree:true, childList:true});
})();
