(() => {
  const nativeFetch = window.fetch.bind(window);
  let lastResult = null;

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[char]));
  const clamp01 = value => Math.max(0, Math.min(1, Number(value) || 0));
  const percent = value => `${Math.round(clamp01(value) * 100)}%`;
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;

  const checkLabels = {
    adaptation_respected: '策略边界',
    evidence_complete: '证据完整',
    evidence_present: '证据存在',
    actions_valid: '工具执行',
    no_failed_actions: '执行稳定',
    critic_ready: 'Critic 就绪',
    constraints_respected: '约束遵循',
    verification_ready: '验证就绪',
  };

  const modeLabels = {
    search: '搜索诊断',
    recommend: '推荐诊断',
    both: '联合诊断',
    audit: '全局体检',
  };

  function resultReward(result) {
    const complete = [...(result.events || [])].reverse().find(event => event.phase === 'complete');
    return complete?.payload?.reward;
  }

  function criticConfidence(result) {
    const direct = result.deliberation?.critic?.confidence;
    if (direct !== undefined && direct !== null) return direct;
    const complete = [...(result.events || [])].reverse().find(event => event.phase === 'complete');
    return complete?.payload?.critic_confidence;
  }

  function telemetryHtml(result) {
    const verification = result.verification || {};
    const autonomy = result.autonomy || {};
    const actions = result.actions || [];
    const evidence = result.evidence || [];
    const completed = actions.filter(action => action.status === 'completed').length;
    const learned = result.evolution?.learned?.length || 0;
    const reward = resultReward(result);
    const critic = criticConfidence(result);
    const passed = verification.passed === true;
    const statusLabel = passed ? 'PASS' : verification.passed === false ? 'REVIEW' : 'DONE';

    const cards = [
      ['验证', statusLabel, passed ? 'verified' : 'neutral'],
      ['执行轮次', autonomy.cycles ?? actions.length, 'neutral'],
      ['工具调用', `${completed}/${actions.length}`, 'neutral'],
      ['证据', evidence.length, 'neutral'],
    ];

    const details = [
      ['Critic', critic === undefined || critic === null ? '—' : percent(critic)],
      ['Reward', reward === undefined || reward === null ? '—' : Number(reward).toFixed(2)],
      ['Memory', `${number(autonomy.memory_hits)} hits`],
      ['Cost', number(autonomy.spent_cost).toFixed(2)],
      ['Learned', learned ? `${learned} 条` : '0'],
    ];

    return `
      <div class="telemetry-grid">
        ${cards.map(([label, value, tone]) => `
          <div class="telemetry-card ${tone}"><span>${esc(label)}</span><b>${esc(value)}</b></div>
        `).join('')}
      </div>
      <div class="telemetry-meta">
        ${details.map(([label, value]) => `<span><i>${esc(label)}</i><b>${esc(value)}</b></span>`).join('')}
      </div>`;
  }

  function verificationHtml(result) {
    const verification = result.verification || {};
    const checks = verification.checks && typeof verification.checks === 'object' ? verification.checks : {};
    const entries = Object.entries(checks).slice(0, 8);
    const critic = criticConfidence(result);

    return `
      <div class="verification-head">
        <div><span>VERIFICATION</span><b>${verification.passed === true ? '独立验证通过' : verification.passed === false ? '需要继续复核' : '验证已完成'}</b></div>
        <strong class="verification-badge ${verification.passed === true ? 'pass' : 'review'}">${verification.passed === true ? 'PASS' : 'CHECK'}</strong>
      </div>
      ${entries.length ? `<div class="verification-checks">${entries.map(([key, value]) => `
        <div><span class="check-dot ${value ? 'ok' : 'warn'}"></span><b>${esc(checkLabels[key] || key.replaceAll('_', ' '))}</b><small>${value ? '通过' : '检查'}</small></div>
      `).join('')}</div>` : ''}
      <div class="verification-foot"><span>Critic confidence</span><b>${critic === undefined || critic === null ? '—' : percent(critic)}</b></div>`;
  }

  function snapshotHtml(result) {
    const evidence = (result.evidence || []).slice(0, 4);
    const findings = (result.findings || []).slice(0, 3);
    const mode = result.plan?.mode || '';
    const learned = result.evolution?.learned?.length || 0;
    const passed = result.verification?.passed === true;

    return `
      <div class="snapshot-head">
        <div>
          <span class="snapshot-eyebrow">RUN SNAPSHOT · ${esc(modeLabels[mode] || mode || '任务')}</span>
          <h3>本次执行的可复核结果</h3>
        </div>
        <span class="snapshot-state ${passed ? 'pass' : 'review'}"><i></i>${passed ? 'Verified' : 'Review'}</span>
      </div>
      <div class="snapshot-body">
        <div class="snapshot-results">
          <span class="snapshot-label">TOP EVIDENCE</span>
          ${evidence.length ? evidence.map((item, index) => `
            <div class="snapshot-result">
              <span>${String(index + 1).padStart(2, '0')}</span>
              <div><b>${esc(item.title || '执行证据')}</b><small>${esc(item.detail || '已复核')}</small></div>
              ${item.score !== undefined ? `<em>${Number(item.score).toFixed(3)}</em>` : ''}
            </div>
          `).join('') : '<div class="snapshot-empty">本次没有结构化结果证据。</div>'}
        </div>
        <div class="snapshot-findings">
          <span class="snapshot-label">FINDINGS</span>
          ${findings.length ? findings.map(item => `<p><i></i><span>${esc(item)}</span></p>`).join('') : '<p><span>本次未发现阻断性问题。</span></p>'}
          ${learned ? `<div class="learning-chip"><span>策略学习</span><b>${learned} 条经验通过门槛</b></div>` : ''}
        </div>
      </div>`;
  }

  function render(result) {
    if (!result || typeof result !== 'object') return;
    lastResult = result;
    const telemetry = $('runTelemetry');
    const verification = $('verificationSummary');
    const snapshot = $('resultSnapshot');
    if (telemetry) {
      telemetry.innerHTML = telemetryHtml(result);
      telemetry.hidden = false;
    }
    if (verification) {
      verification.innerHTML = verificationHtml(result);
      verification.hidden = false;
    }
    if (snapshot) {
      snapshot.innerHTML = snapshotHtml(result);
      snapshot.hidden = false;
    }
  }

  function clear() {
    lastResult = null;
    ['runTelemetry', 'verificationSummary', 'resultSnapshot'].forEach(id => {
      const node = $(id);
      if (node) {
        node.hidden = true;
        node.innerHTML = '';
      }
    });
  }

  function inspectPayload(path, payload) {
    if (!payload || typeof payload !== 'object') return;
    if (path.startsWith('/api/runs/') && payload.status === 'completed' && payload.result) {
      requestAnimationFrame(() => render(payload.result));
      return;
    }
    if (/^\/api\/conversations\/[^/]+$/.test(path) && Array.isArray(payload.messages)) {
      const assistant = [...payload.messages].reverse().find(message => message.role === 'assistant' && message.payload);
      if (assistant?.payload) requestAnimationFrame(() => render(assistant.payload));
    }
  }

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
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
    if (event.target.closest('#newTaskBtn, .scene')) clear();
  });

  const observer = new MutationObserver(() => {
    if (lastResult && $('resultSnapshot')?.hidden) render(lastResult);
  });
  observer.observe(document.body, {subtree:true, childList:true});
})();
