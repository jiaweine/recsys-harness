(() => {
  const upstreamFetch = window.fetch.bind(window);
  let lastCompletedResult = null;
  let latestRunStatus = null;

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'
  }[char]));
  const num = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const pct = value => `${Math.round(Math.max(0, Math.min(1, num(value))) * 100)}%`;

  const phaseMeta = {
    observe: ['观察', 'observe'],
    perceive: ['附件', 'observe'],
    memory: ['经验', 'memory'],
    guard: ['约束', 'guard'],
    deliberate: ['Mission', 'mission'],
    decide: ['Decision', 'decision'],
    execute: ['Tool', 'execute'],
    reflect: ['Reflection', 'reflect'],
    verify: ['Verify', 'verify'],
    complete: ['Complete', 'complete'],
    resume: ['Resume', 'observe'],
  };

  const toolLabels = {
    'data.inspect': '工作区检查',
    'search.run': '搜索复现',
    'search.diagnose': '搜索诊断',
    'search.audit': '搜索评估',
    'search.evolve': '搜索策略探索',
    'recommend.run': '推荐复现',
    'recommend.diagnose': '推荐诊断',
    'recommend.audit': '推荐评估',
    'recommend.evolve': '推荐策略探索',
    'web.research': '公开资料研究',
  };

  const riskLabels = {
    read: '只读',
    simulation: '模拟',
    adaptive: '策略变更',
    network: '联网',
  };

  const requirementStatus = {
    open: '待满足',
    satisfied: '已满足',
    complete: '已满足',
    blocked: '受阻',
    dormant: '暂不需要',
  };

  const hypothesisStatus = {
    open: '观察中',
    supported: '被支持',
    weakened: '减弱',
    rejected: '已排除',
    retired: '已退出',
  };

  function ensureMounts() {
    const telemetry = $('runTelemetry');
    const timeline = $('timeline');
    if (!telemetry || !timeline || $('agentTrace')) return;

    const mission = document.createElement('section');
    mission.id = 'missionSummary';
    mission.className = 'mission-summary';
    mission.hidden = true;

    const trace = document.createElement('section');
    trace.id = 'agentTrace';
    trace.className = 'agent-trace';
    trace.hidden = true;
    trace.setAttribute('aria-live', 'polite');

    timeline.parentNode.insertBefore(mission, timeline);
    timeline.parentNode.insertBefore(trace, timeline);
  }

  function toolLabel(tool) {
    return toolLabels[tool] || String(tool || '').replaceAll('.', ' · ') || '执行动作';
  }

  function relativeTime(timestamp, first) {
    const value = Number(timestamp);
    const start = Number(first);
    if (!Number.isFinite(value) || !Number.isFinite(start)) return '';
    const delta = Math.max(0, value - start);
    return delta < 10 ? `+${delta.toFixed(1)}s` : `+${Math.round(delta)}s`;
  }

  function statusTone(status) {
    if (['satisfied', 'complete', 'supported'].includes(status)) return 'pass';
    if (['blocked', 'rejected'].includes(status)) return 'warn';
    if (['dormant', 'retired'].includes(status)) return 'quiet';
    return 'active';
  }

  function missionHtml(result) {
    const mission = result?.deliberation?.mission;
    if (!mission || typeof mission !== 'object') return '';
    const requirements = Object.values(mission.requirements || {});
    const hypotheses = Object.values(mission.hypotheses || {});
    const satisfied = requirements.filter(row => ['satisfied', 'complete'].includes(row.status)).length;
    const activeHypotheses = hypotheses.filter(row => !['rejected', 'retired'].includes(row.status)).length;

    return `
      <div class="mission-head">
        <div>
          <span>MISSION GRAPH</span>
          <h3>${esc(mission.objective || '本次任务证据图')}</h3>
        </div>
        <div class="mission-stats">
          <span><b>${satisfied}/${requirements.length}</b><i>证据需求</i></span>
          <span><b>${activeHypotheses}</b><i>活跃假设</i></span>
        </div>
      </div>
      ${requirements.length ? `<div class="mission-requirements">
        <span class="trace-section-label">EVIDENCE REQUIREMENTS</span>
        ${requirements.slice(0, 8).map(row => `
          <div class="mission-requirement">
            <span class="mission-state ${statusTone(row.status)}"><i></i>${esc(requirementStatus[row.status] || row.status || '待满足')}</span>
            <div><b>${esc(row.label || row.key || '证据需求')}</b><small>${esc(row.reason || toolLabel(row.tool))}</small></div>
            <em>${esc(row.priority || 'medium')}</em>
          </div>`).join('')}
      </div>` : ''}
      ${hypotheses.length ? `<div class="mission-hypotheses">
        <span class="trace-section-label">HYPOTHESES</span>
        ${hypotheses.slice(0, 6).map(row => {
          const level = Math.round(Math.max(0, Math.min(1, num(row.confidence))) * 10);
          return `<div class="hypothesis-row">
            <div><b>${esc(row.label || row.key || '待验证假设')}</b><small>${esc(hypothesisStatus[row.status] || row.status || '观察中')}</small></div>
            <span class="confidence-bar" data-level="${level}"><i></i></span>
            <em>${pct(row.confidence)}</em>
          </div>`;
        }).join('')}
      </div>` : ''}
      ${(mission.exit_criteria || []).length ? `<div class="mission-exit"><span>结束条件</span><p>${(mission.exit_criteria || []).slice(0, 3).map(esc).join(' · ')}</p></div>` : ''}`;
  }

  function resultSummary(action) {
    if (!action || action.status === 'failed') return action?.error ? `执行失败：${action.error}` : '';
    const result = action.result || {};
    const tool = action.tool;
    if (tool === 'search.run' || tool === 'recommend.run') {
      const rows = Array.isArray(result.results) ? result.results : [];
      const top = rows[0]?.title || rows[0]?.id;
      return `${rows.length} 条结果${top ? `，首位为“${top}”` : ''}`;
    }
    if (tool === 'search.diagnose' || tool === 'recommend.diagnose') return result.diagnosis || '诊断完成';
    if (tool === 'search.audit') return `综合质量 ${pct(result.quality)} · 相关覆盖 ${pct(result.recall)}`;
    if (tool === 'recommend.audit') return `综合质量 ${pct(result.quality)} · 内容覆盖 ${pct(result.coverage)} · 新鲜度 ${pct(result.freshness)}`;
    if (tool === 'search.evolve' || tool === 'recommend.evolve') {
      if (!result.evaluation_ready) return '评估证据不足，候选策略未进入后续门槛';
      if (result.trusted) return result.activated ? '候选策略通过验证并已启用' : '候选策略通过验证，已保留为可信经验';
      if (result.safe_to_try) return '候选策略结构安全，但优势不足以晋升';
      return '候选策略未通过稳健性门槛';
    }
    if (tool === 'data.inspect') {
      const summary = result.summary || {};
      const issues = Array.isArray(result.issues) ? result.issues.length : 0;
      return `${num(summary.items)} 条内容 · ${num(summary.users)} 个用户 · ${issues} 个数据提醒`;
    }
    if (tool === 'web.research') return `补充 ${Array.isArray(result.results) ? result.results.length : 0} 条公开资料`;
    return '';
  }

  function criticFacts(critic) {
    if (!critic || typeof critic !== 'object') return [];
    const rows = [];
    if (critic.confidence !== undefined) rows.push(['Critic', pct(critic.confidence)]);
    if (critic.evidence_coverage !== undefined) rows.push(['证据覆盖', pct(critic.evidence_coverage)]);
    if (critic.terminal_coverage !== undefined) rows.push(['终止覆盖', pct(critic.terminal_coverage)]);
    if (critic.ready !== undefined) rows.push(['收敛', critic.ready ? '是' : '否']);
    return rows;
  }

  function traceChips(items, className = '') {
    const rows = (items || []).filter(Boolean).slice(0, 6);
    if (!rows.length) return '';
    return `<div class="trace-chips ${className}">${rows.map(item => `<span>${esc(item)}</span>`).join('')}</div>`;
  }

  function traceFacts(rows) {
    const values = (rows || []).filter(row => row && row[1] !== undefined && row[1] !== null && row[1] !== '');
    if (!values.length) return '';
    return `<div class="trace-facts">${values.map(([key, value]) => `<span><i>${esc(key)}</i><b>${esc(value)}</b></span>`).join('')}</div>`;
  }

  function normalizeEvents(events, actions) {
    let cycle = 0;
    let executeIndex = 0;
    return (events || []).map((event, index) => {
      const payload = event.payload || {};
      if (event.phase === 'decide') cycle = Number(payload.cycle) || cycle + 1;
      const eventCycle = ['decide', 'execute', 'reflect'].includes(event.phase) ? cycle : null;
      let action = null;
      if (event.phase === 'execute') {
        action = actions?.[executeIndex] || null;
        executeIndex += 1;
      }
      return {event, payload, index, cycle:eventCycle, action};
    });
  }

  function alternativeRows(alternatives) {
    const rows = Array.isArray(alternatives) ? alternatives.slice(0, 3) : [];
    if (!rows.length) return '';
    return `<div class="trace-alternatives"><span>其他候选动作</span>${rows.map(row => {
      const tool = row.tool || row.name || row.action || '';
      const score = row.score ?? row.utility ?? row.value;
      return `<div><b>${esc(toolLabel(tool))}</b><small>${Number.isFinite(Number(score)) ? Number(score).toFixed(3) : '候选'}</small></div>`;
    }).join('')}</div>`;
  }

  function traceBody(entry) {
    const {event, payload, action} = entry;
    const parts = [];

    if (event.phase === 'decide') {
      parts.push(traceFacts([
        ['目标需求', payload.requirement || '当前最高价值缺口'],
        ['决策得分', Number.isFinite(Number(payload.score)) ? Number(payload.score).toFixed(3) : '—'],
        ['经验增益', Number.isFinite(Number(payload.learned_bonus)) ? Number(payload.learned_bonus).toFixed(3) : '—'],
      ]));
      parts.push(traceChips(payload.hypotheses, 'hypothesis-chips'));
      parts.push(alternativeRows(payload.alternatives));
    }

    if (event.phase === 'execute') {
      parts.push(traceFacts([
        ['动作', toolLabel(payload.tool || action?.tool)],
        ['风险', riskLabels[payload.risk || action?.risk] || payload.risk || action?.risk || '—'],
        ['成本', payload.cost ?? action?.cost ?? '—'],
        ['目标需求', payload.requirement || action?.decision?.requirement || '—'],
      ]));
      const observation = resultSummary(action);
      if (observation) parts.push(`<div class="trace-observation"><span>OBSERVATION</span><p>${esc(observation)}</p></div>`);
    }

    if (event.phase === 'reflect') {
      parts.push(traceFacts([
        ['需求变化', (payload.requirements_changed || []).length],
        ['假设变化', (payload.hypotheses_changed || []).length],
        ...criticFacts(payload.critic),
      ]));
      if ((payload.next_gaps || []).length) parts.push(`<div class="trace-gap"><span>NEXT EVIDENCE GAPS</span>${traceChips(payload.next_gaps)}</div>`);
    }

    if (event.phase === 'verify') {
      parts.push(traceFacts(criticFacts(payload.critic)));
      const blocked = payload.critic?.blocked || [];
      const unresolved = payload.critic?.unresolved || [];
      if (blocked.length) parts.push(`<div class="trace-gap warn"><span>BLOCKED</span>${traceChips(blocked)}</div>`);
      if (unresolved.length) parts.push(`<div class="trace-gap warn"><span>UNRESOLVED</span>${traceChips(unresolved)}</div>`);
    }

    if (event.phase === 'complete') {
      parts.push(traceFacts([
        ['Reward', Number.isFinite(Number(payload.reward)) ? Number(payload.reward).toFixed(2) : '—'],
        ['Learned', payload.learned ?? 0],
        ['Critic', payload.critic_confidence === undefined ? '—' : pct(payload.critic_confidence)],
      ]));
    }

    if (event.phase === 'memory') {
      parts.push(traceFacts([['相关经验', Array.isArray(payload.memories) ? payload.memories.length : 0]]));
    }

    if (event.phase === 'deliberate') {
      parts.push(traceFacts([
        ['初始证据需求', Array.isArray(payload.requirements) ? payload.requirements.length : 0],
        ['可更新假设', Array.isArray(payload.hypotheses) ? payload.hypotheses.length : 0],
      ]));
    }

    if (event.phase === 'guard') {
      parts.push(traceFacts([
        ['允许策略变化', payload.allow_adaptation ? '是' : '否'],
        ['允许联网', payload.allow_network ? '是' : '否'],
      ]));
    }

    return parts.filter(Boolean).join('');
  }

  function traceHtml(events, actions, status = 'running') {
    const normalized = normalizeEvents(events, actions);
    if (!normalized.length) return '';
    const firstTime = normalized[0]?.event?.created_at;
    const cycles = Math.max(0, ...normalized.map(row => row.cycle || 0));
    const completed = status === 'completed';

    return `
      <div class="trace-head">
        <div><span>AGENT TRACE</span><h3>${completed ? '可复核执行轨迹' : '实时执行轨迹'}</h3></div>
        <div class="trace-head-actions">
          <span class="trace-live ${completed ? 'done' : ''}"><i></i>${completed ? '完成' : 'LIVE'}</span>
          <button type="button" id="traceToggleAll">展开全部</button>
        </div>
      </div>
      <div class="trace-overview">
        <span><b>${normalized.length}</b><i>事件</i></span>
        <span><b>${cycles}</b><i>执行轮次</i></span>
        <span><b>${(actions || []).filter(row => row.status === 'completed').length}</b><i>已完成动作</i></span>
      </div>
      <div class="trace-list">
        ${normalized.map((entry, index) => {
          const {event, payload, cycle} = entry;
          const [label, tone] = phaseMeta[event.phase] || [event.phase || 'Event', 'observe'];
          const latest = index === normalized.length - 1;
          const autoOpen = completed ? ['verify', 'complete'].includes(event.phase) : latest;
          const tool = event.phase === 'execute' ? toolLabel(payload.tool || entry.action?.tool) : '';
          return `<details class="trace-step ${tone}" ${autoOpen ? 'open' : ''}>
            <summary>
              <span class="trace-node"><i></i></span>
              <span class="trace-phase">${esc(label)}</span>
              <div class="trace-summary-copy"><b>${esc(event.title || label)}</b><small>${esc(tool || event.detail || '')}</small></div>
              ${cycle ? `<span class="trace-cycle">C${String(cycle).padStart(2, '0')}</span>` : ''}
              <time>${esc(relativeTime(event.created_at, firstTime))}</time>
              <span class="trace-caret">›</span>
            </summary>
            <div class="trace-detail">
              ${event.detail ? `<p>${esc(event.detail)}</p>` : ''}
              ${traceBody(entry)}
            </div>
          </details>`;
        }).join('')}
      </div>`;
  }

  function renderMission(result) {
    ensureMounts();
    const node = $('missionSummary');
    if (!node) return;
    const html = missionHtml(result);
    node.innerHTML = html;
    node.hidden = !html;
  }

  function renderTrace(events, actions = [], status = 'running') {
    ensureMounts();
    const node = $('agentTrace');
    if (!node) return;
    const html = traceHtml(events, actions, status);
    node.innerHTML = html;
    node.hidden = !html;
    const timeline = $('timeline');
    if (timeline) timeline.setAttribute('aria-hidden', html ? 'true' : 'false');
  }

  function renderCompleted(result) {
    if (!result || typeof result !== 'object') return;
    lastCompletedResult = result;
    latestRunStatus = 'completed';
    renderMission(result);
    renderTrace(result.events || [], result.actions || [], 'completed');
  }

  function clearTrace() {
    lastCompletedResult = null;
    latestRunStatus = null;
    ['missionSummary', 'agentTrace'].forEach(id => {
      const node = $(id);
      if (node) {
        node.hidden = true;
        node.innerHTML = '';
      }
    });
    const timeline = $('timeline');
    if (timeline) timeline.removeAttribute('aria-hidden');
  }

  function inspectPayload(path, payload) {
    if (!payload || typeof payload !== 'object') return;
    if (path.startsWith('/api/runs/')) {
      latestRunStatus = payload.status || 'running';
      if (payload.status === 'completed' && payload.result) {
        requestAnimationFrame(() => renderCompleted(payload.result));
      } else if (Array.isArray(payload.events)) {
        requestAnimationFrame(() => renderTrace(payload.events, [], payload.status || 'running'));
      }
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
    if (event.target.closest('#newTaskBtn, .scene, .history-item')) clearTrace();
    const toggle = event.target.closest('#traceToggleAll');
    if (toggle) {
      const details = [...document.querySelectorAll('#agentTrace details.trace-step')];
      const shouldOpen = details.some(item => !item.open);
      details.forEach(item => { item.open = shouldOpen; });
      toggle.textContent = shouldOpen ? '收起全部' : '展开全部';
    }
  });

  ensureMounts();
  const observer = new MutationObserver(() => {
    ensureMounts();
    if (lastCompletedResult && $('agentTrace')?.hidden) renderCompleted(lastCompletedResult);
    if (latestRunStatus && latestRunStatus !== 'completed' && $('agentTrace')?.hidden) {
      // app.js may update neighboring fallback timeline; keep the richer trace mounted.
      $('agentTrace').hidden = false;
    }
  });
  observer.observe(document.body, {subtree:true, childList:true});
})();
