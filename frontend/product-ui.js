(() => {
  const nativeFetch = window.fetch.bind(window);
  let lastResult = null;

  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[char]));
  const clamp01 = value => Math.max(0, Math.min(1, Number(value) || 0));
  const percent = value => `${Math.round(clamp01(value) * 100)}%`;
  const signedPercent = value => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) return '—';
    const sign = numeric > 0 ? '+' : '';
    return `${sign}${(numeric * 100).toFixed(1)}%`;
  };
  const number = value => Number.isFinite(Number(value)) ? Number(value) : 0;

  const checkLabels = {
    executed_tools: '工具已执行',
    no_failed_tools: '无失败工具',
    evidence_backed: '证据支撑',
    adaptation_respected: '策略边界',
    mission_terminal: 'Mission 收敛',
    contradictions_resolved: '矛盾已解决',
  };

  const modeLabels = {
    search: '搜索诊断',
    recommend: '推荐诊断',
    both: '联合诊断',
    audit: '全局体检',
  };

  const signalLabels = {
    search: [
      ['match', '匹配'],
      ['quality', '质量'],
      ['freshness', '新鲜'],
      ['popularity', '热度'],
    ],
    recommend: [
      ['fit', '贴合'],
      ['quality', '质量'],
      ['freshness', '新鲜'],
      ['novelty', '新颖'],
    ],
  };

  const experimentMetrics = {
    search: [
      ['quality', '综合质量'],
      ['recall', '相关覆盖'],
    ],
    recommend: [
      ['quality', '综合质量'],
      ['coverage', '内容覆盖'],
      ['freshness', '新鲜度'],
      ['diversity', '分散度'],
      ['cold_start_quality', '冷启动质量'],
    ],
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

  function lastAction(result, tool) {
    return [...(result.actions || [])].reverse().find(action => action.tool === tool && action.status === 'completed');
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
      ['Verifier', verification.confidence === undefined ? '—' : percent(verification.confidence)],
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

    return `
      <div class="verification-head">
        <div><span>VERIFICATION · ${verification.confidence === undefined ? '—' : percent(verification.confidence)}</span><b>${verification.passed === true ? '独立验证通过' : verification.passed === false ? '需要继续复核' : '验证已完成'}</b></div>
        <strong class="verification-badge ${verification.passed === true ? 'pass' : 'review'}">${verification.passed === true ? 'PASS' : 'CHECK'}</strong>
      </div>
      ${entries.length ? `<div class="verification-checks">${entries.map(([key, value]) => `
        <div><span class="check-dot ${value === true ? 'ok' : 'warn'}"></span><b>${esc(checkLabels[key] || key.replaceAll('_', ' '))}</b><small>${value === true ? '通过' : '检查'}</small></div>
      `).join('')}</div>` : ''}
      <div class="verification-foot"><span>Trajectory gate</span><b>${verification.passed === true ? 'all checks passed' : 'review required'}</b></div>`;
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

  function signalHtml(domain, signals = {}) {
    return (signalLabels[domain] || []).map(([key, label]) => {
      const value = clamp01(signals[key]);
      const level = Math.round(value * 10);
      return `<span class="result-signal" data-level="${level}"><i>${esc(label)}</i><b>${percent(value)}</b><em><u></u></em></span>`;
    }).join('');
  }

  function resultFlags(domain, row) {
    const flags = [];
    if (domain === 'search' && number(row.signals?.match) < 0.42) flags.push('匹配偏弱');
    return flags;
  }

  function diagnosisHtml(domain, diag) {
    const data = diag?.result || {};
    if (!diag || !data || typeof data !== 'object') return '';
    const meta = domain === 'search'
      ? [
          ['结果数', data.result_count ?? '—'],
          ['首位匹配', data.top_match === undefined ? '—' : percent(data.top_match)],
          ['查询证据', `${(data.covered_tokens || []).length}/${(data.query_tokens || []).length || 0}`],
        ]
      : [
          ['历史行为', data.history_events ?? 0],
          ['未看候选', data.eligible_unseen ?? 0],
          ['用户状态', data.cold_start ? '冷启动' : '已有画像'],
        ];
    const warnings = [];
    if (domain === 'recommend' && data.cold_start) warnings.push('冷启动');
    if (domain === 'recommend' && number(data.eligible_unseen) < 8) warnings.push('候选池偏小');
    if (domain === 'search' && number(data.top_match) < 0.42) warnings.push('首位匹配偏弱');

    return `
      <div class="diagnosis-strip">
        <div class="diagnosis-copy"><span>DIAGNOSIS</span><b>${esc(data.diagnosis || '已完成结果诊断')}</b></div>
        <div class="diagnosis-meta">${meta.map(([label, value]) => `<span><i>${esc(label)}</i><b>${esc(value)}</b></span>`).join('')}</div>
        ${warnings.length ? `<div class="diagnosis-flags">${warnings.map(flag => `<span>${esc(flag)}</span>`).join('')}</div>` : ''}
      </div>`;
  }

  function resultBlockHtml(domain, runAction, diagAction) {
    const data = runAction?.result || {};
    const rows = Array.isArray(data.results) ? data.results.slice(0, 8) : [];
    const subject = domain === 'search' ? data.query : data.user_id;
    const title = domain === 'search' ? '搜索结果解释' : '推荐首屏解释';
    const eyebrow = domain === 'search' ? 'SEARCH RESULT' : 'RECOMMENDATION SLATE';
    if (!rows.length) return '';

    return `
      <div class="result-analysis-block">
        <div class="analysis-head">
          <div><span>${eyebrow}</span><h3>${esc(title)}</h3></div>
          <b>${esc(subject || '当前任务')}</b>
        </div>
        ${diagnosisHtml(domain, diagAction)}
        <div class="rank-table">
          ${rows.map(row => {
            const flags = resultFlags(domain, row);
            const categories = Array.isArray(row.categories) ? row.categories.slice(0, 2) : [];
            return `<div class="rank-row">
              <span class="rank-index">${String(row.rank ?? '').padStart(2, '0')}</span>
              <div class="rank-main">
                <div class="rank-title"><b>${esc(row.title || row.id || '结果')}</b>${flags.map(flag => `<span>${esc(flag)}</span>`).join('')}</div>
                <small>${categories.map(esc).join(' · ') || esc(row.id || '')}</small>
              </div>
              <div class="rank-signals">${signalHtml(domain, row.signals)}</div>
              <em class="rank-score">${Number(row.score || 0).toFixed(3)}</em>
            </div>`;
          }).join('')}
        </div>
      </div>`;
  }

  function resultAnalysisHtml(result) {
    const blocks = [];
    const searchRun = lastAction(result, 'search.run');
    const recommendRun = lastAction(result, 'recommend.run');
    if (searchRun) blocks.push(resultBlockHtml('search', searchRun, lastAction(result, 'search.diagnose')));
    if (recommendRun) blocks.push(resultBlockHtml('recommend', recommendRun, lastAction(result, 'recommend.diagnose')));
    return blocks.filter(Boolean).join('');
  }

  function gateChip(label, value, neutral = false) {
    const tone = neutral ? 'neutral' : value ? 'pass' : 'warn';
    return `<span class="experiment-gate ${tone}"><i></i>${esc(label)}</span>`;
  }

  function experimentStatus(data) {
    if (!data.evaluation_ready) return ['证据不足', 'neutral'];
    if (data.trusted) return [data.activated ? '已验证并启用' : '已验证，可沉淀', 'pass'];
    if (data.safe_to_try) return ['结构安全，优势不足', 'neutral'];
    return ['未通过稳健门槛', 'warn'];
  }

  function experimentBlockHtml(domain, action) {
    const data = action?.result || {};
    if (!action || !data || typeof data !== 'object') return '';
    const metrics = experimentMetrics[domain] || [];
    const [status, tone] = experimentStatus(data);
    const holdout = data.validation?.holdout || {};
    const robustness = data.robustness || {};
    const label = domain === 'search' ? '搜索策略实验' : '推荐策略实验';

    return `
      <div class="experiment-block">
        <div class="experiment-head">
          <div><span>STRATEGY EXPERIMENT</span><h3>${esc(label)}</h3></div>
          <strong class="experiment-status ${tone}">${esc(status)}</strong>
        </div>
        <div class="experiment-gates">
          ${gateChip('评估证据', !!data.evaluation_ready)}
          ${gateChip('安全门槛', !!data.safe_to_try)}
          ${gateChip('稳健验证', !!data.trusted)}
          ${gateChip(data.activated ? '当前已启用' : '未改变当前策略', true)}
        </div>
        <div class="metric-compare-head"><span>指标</span><span>当前</span><span>候选</span><span>变化</span></div>
        <div class="metric-compare">
          ${metrics.map(([key, labelText]) => {
            const before = data.reference?.[key];
            const after = data.candidate?.[key];
            const delta = data.delta?.[key];
            const deltaNumber = Number(delta);
            const deltaTone = Number.isFinite(deltaNumber) && deltaNumber > 0 ? 'up' : Number.isFinite(deltaNumber) && deltaNumber < 0 ? 'down' : 'flat';
            return `<div><b>${esc(labelText)}</b><span>${before === undefined ? '—' : percent(before)}</span><span>${after === undefined ? '—' : percent(after)}</span><em class="${deltaTone}">${signedPercent(delta)}</em></div>`;
          }).join('')}
        </div>
        <div class="experiment-foot">
          <span><i>候选规模</i><b>${number(data.candidate_count)}</b></span>
          <span><i>探索轮次</i><b>${number(data.generations)}</b></span>
          <span><i>独立样本</i><b>${number(holdout.samples)}</b></span>
          <span><i>退化样本</i><b>${robustness.worse_share === undefined ? '—' : percent(robustness.worse_share)}</b></span>
          <span><i>最差变化</i><b>${robustness.worst_delta === undefined ? '—' : signedPercent(robustness.worst_delta)}</b></span>
        </div>
      </div>`;
  }

  function strategyExperimentHtml(result) {
    const blocks = [];
    const search = lastAction(result, 'search.evolve');
    const recommend = lastAction(result, 'recommend.evolve');
    if (search) blocks.push(experimentBlockHtml('search', search));
    if (recommend) blocks.push(experimentBlockHtml('recommend', recommend));
    return blocks.filter(Boolean).join('');
  }

  function ensureProductSurfaces() {
    const snapshot = $('resultSnapshot');
    if (!snapshot) return;
    let analysis = $('resultAnalysis');
    if (!analysis) {
      analysis = document.createElement('section');
      analysis.id = 'resultAnalysis';
      analysis.className = 'result-analysis';
      analysis.hidden = true;
      analysis.setAttribute('aria-live', 'polite');
      snapshot.insertAdjacentElement('afterend', analysis);
    }
    let experiment = $('strategyExperiment');
    if (!experiment) {
      experiment = document.createElement('section');
      experiment.id = 'strategyExperiment';
      experiment.className = 'strategy-experiment';
      experiment.hidden = true;
      experiment.setAttribute('aria-live', 'polite');
      analysis.insertAdjacentElement('afterend', experiment);
    }
  }

  function render(result) {
    if (!result || typeof result !== 'object') return;
    lastResult = result;
    const telemetry = $('runTelemetry');
    const verification = $('verificationSummary');
    const snapshot = $('resultSnapshot');
    const analysis = $('resultAnalysis');
    const experiment = $('strategyExperiment');
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
    if (analysis) {
      const html = resultAnalysisHtml(result);
      analysis.innerHTML = html;
      analysis.hidden = !html;
    }
    if (experiment) {
      const html = strategyExperimentHtml(result);
      experiment.innerHTML = html;
      experiment.hidden = !html;
    }
  }

  function clear() {
    lastResult = null;
    ['runTelemetry', 'verificationSummary', 'resultSnapshot', 'resultAnalysis', 'strategyExperiment'].forEach(id => {
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
    if (event.target.closest('#newTaskBtn, .scene, .history-item')) clear();
  });

  ensureProductSurfaces();

  const observer = new MutationObserver(() => {
    if (lastResult && $('resultSnapshot')?.hidden) render(lastResult);
  });
  observer.observe(document.body, {subtree:true, childList:true});
})();
