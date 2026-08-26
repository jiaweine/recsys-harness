(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[char]));
  let currentConversationId = null;
  let generation = 0;
  let triggerQueued = false;

  function numberOrNull(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function signed(value, digits = 0, suffix = '') {
    const numeric = numberOrNull(value);
    if (numeric === null) return '—';
    const sign = numeric > 0 ? '+' : '';
    return `${sign}${numeric.toFixed(digits)}${suffix}`;
  }

  function percent(value) {
    const numeric = numberOrNull(value);
    return numeric === null ? '—' : `${Math.round(Math.max(0, Math.min(1, numeric)) * 100)}%`;
  }

  function xhrJson(path) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('GET', path, true);
      request.responseType = 'json';
      request.withCredentials = true;
      request.timeout = 9000;
      request.onload = () => {
        if (request.status >= 200 && request.status < 300) {
          const payload = request.response ?? (() => {
            try { return JSON.parse(request.responseText || 'null'); } catch { return null; }
          })();
          resolve(payload);
          return;
        }
        reject(new Error(request.status === 401 ? '需要重新进入工作区' : `历史任务读取失败 · ${request.status}`));
      };
      request.onerror = () => reject(new Error('历史任务读取失败'));
      request.ontimeout = () => reject(new Error('历史任务读取超时'));
      request.send();
    });
  }

  function lastAction(result, tool) {
    return [...(result?.actions || [])].reverse().find(action => action.tool === tool && action.status === 'completed');
  }

  function resultContext(result) {
    const search = lastAction(result, 'search.run');
    const recommend = lastAction(result, 'recommend.run');
    const mode = String(result?.plan?.mode || '');
    if (search && !recommend) {
      const key = String(search.result?.query || '').trim().toLowerCase();
      return {domain:'search', key, label:String(search.result?.query || '当前搜索')};
    }
    if (recommend && !search) {
      const key = String(recommend.result?.user_id || '').trim().toLowerCase();
      return {domain:'recommend', key, label:String(recommend.result?.user_id || '当前用户')};
    }
    if (mode === 'search' && search) {
      const key = String(search.result?.query || '').trim().toLowerCase();
      return {domain:'search', key, label:String(search.result?.query || '当前搜索')};
    }
    if (mode === 'recommend' && recommend) {
      const key = String(recommend.result?.user_id || '').trim().toLowerCase();
      return {domain:'recommend', key, label:String(recommend.result?.user_id || '当前用户')};
    }
    return {domain:search && recommend ? 'both' : mode || 'run', key:'', label:'当前任务'};
  }

  function validResult(payload) {
    return !!payload && typeof payload === 'object' && (
      Array.isArray(payload.actions) || payload.verification || Array.isArray(payload.events)
    );
  }

  function assistantResults(conversation) {
    return (conversation?.messages || [])
      .filter(message => message.role === 'assistant' && validResult(message.payload))
      .map(message => ({result:message.payload, created_at:numberOrNull(message.created_at) || 0}));
  }

  function runReward(result) {
    const event = [...(result?.events || [])].reverse().find(row => row.phase === 'complete');
    return numberOrNull(event?.payload?.reward);
  }

  function runStats(result) {
    const actions = Array.isArray(result?.actions) ? result.actions : [];
    const autonomy = result?.autonomy || {};
    return {
      confidence:numberOrNull(result?.verification?.confidence),
      cycles:numberOrNull(autonomy.cycles ?? actions.length),
      tools:actions.filter(action => action.status === 'completed').length,
      evidence:Array.isArray(result?.evidence) ? result.evidence.length : 0,
      cost:numberOrNull(autonomy.spent_cost),
      reward:runReward(result),
      memory:numberOrNull(autonomy.memory_hits),
    };
  }

  function verificationStatus(result) {
    if (result?.verification?.passed === true) return {label:'PASS', tone:'pass'};
    if (result?.verification?.passed === false) return {label:'CHECK', tone:'review'};
    return {label:'DONE', tone:''};
  }

  function formatTime(value) {
    const numeric = numberOrNull(value);
    if (numeric === null) return '';
    try {
      return new Date(numeric * 1000).toLocaleString('zh-CN', {month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'});
    } catch { return ''; }
  }

  function collectCandidates(conversation, row, {skipLatest = false} = {}) {
    const results = assistantResults(conversation);
    const usable = skipLatest ? results.slice(0, -1) : results;
    return usable.map(entry => ({
      row,
      result:entry.result,
      context:resultContext(entry.result),
      timestamp:entry.created_at || numberOrNull(row?.updated_at) || 0,
      sameTarget:false,
    }));
  }

  async function findBaseline(snapshot, currentConversation) {
    const target = resultContext(snapshot.result);
    const candidates = collectCandidates(
      currentConversation,
      {id:snapshot.conversation_id, title:currentConversation?.title || '当前任务', updated_at:currentConversation?.updated_at},
      {skipLatest:true},
    );

    const rows = await xhrJson('/api/conversations');
    if (Array.isArray(rows)) {
      const recent = rows
        .filter(row => row?.id && row.id !== snapshot.conversation_id && !row.active && row.scene === snapshot.scene)
        .slice(0, 8);
      for (const row of recent) {
        try {
          const conversation = await xhrJson(`/api/conversations/${encodeURIComponent(row.id)}`);
          candidates.push(...collectCandidates(conversation, row));
        } catch {
          // Missing history is not allowed to disturb the current run.
        }
      }
    }

    candidates.sort((left, right) => right.timestamp - left.timestamp);
    if (target.key) {
      const matched = candidates.find(candidate => (
        candidate.context.domain === target.domain && candidate.context.key === target.key
      ));
      if (matched) {
        matched.sameTarget = true;
        return matched;
      }
    }
    return candidates[0] || null;
  }

  function metricRow(label, previous, latest, kind = 'number') {
    const prev = numberOrNull(previous);
    const next = numberOrNull(latest);
    let before = '—';
    let after = '—';
    let delta = '—';
    if (kind === 'percent') {
      before = percent(prev);
      after = percent(next);
      if (prev !== null && next !== null) delta = signed((next - prev) * 100, 1, 'pp');
    } else if (kind === 'decimal') {
      before = prev === null ? '—' : prev.toFixed(2);
      after = next === null ? '—' : next.toFixed(2);
      if (prev !== null && next !== null) delta = signed(next - prev, 2);
    } else {
      before = prev === null ? '—' : String(Math.round(prev));
      after = next === null ? '—' : String(Math.round(next));
      if (prev !== null && next !== null) delta = signed(next - prev, 0);
    }
    const changed = prev !== null && next !== null && Math.abs(next - prev) > 1e-9;
    return `<div class="compare-metric"><b>${esc(label)}</b><span>${esc(before)}</span><span>${esc(after)}</span><em class="${changed ? 'changed' : 'quiet'}">${esc(delta)}</em></div>`;
  }

  function verificationHtml(previous, latest) {
    const prev = verificationStatus(previous);
    const next = verificationStatus(latest);
    return `<div class="compare-verification">
      <div><span>上次验证</span><b class="${prev.tone}"><i></i>${prev.label} · ${percent(previous?.verification?.confidence)}</b></div>
      <div><span>本次验证</span><b class="${next.tone}"><i></i>${next.label} · ${percent(latest?.verification?.confidence)}</b></div>
    </div>`;
  }

  function rankingData(result, domain) {
    const action = lastAction(result, domain === 'search' ? 'search.run' : 'recommend.run');
    const rows = Array.isArray(action?.result?.results) ? action.result.results.slice(0, 8) : [];
    return rows.map((row, index) => ({
      id:String(row.id || row.item_id || row.title || ''),
      title:String(row.title || row.id || row.item_id || '结果'),
      rank:numberOrNull(row.rank) ?? index + 1,
      score:numberOrNull(row.score),
      categories:Array.isArray(row.categories) ? row.categories.slice(0, 2).map(String) : [],
    })).filter(row => row.id);
  }

  function rankingHtml(previous, latest, context) {
    if (!context.sameTarget || !['search','recommend'].includes(context.current.domain)) return '';
    const before = rankingData(previous, context.current.domain);
    const after = rankingData(latest, context.current.domain);
    if (!before.length || !after.length) return '';
    const beforeMap = new Map(before.map(row => [row.id, row]));
    const afterIds = new Set(after.map(row => row.id));
    const dropped = before.filter(row => !afterIds.has(row.id)).length;
    const rows = after.map(row => {
      const old = beforeMap.get(row.id);
      const oldRank = old?.rank;
      const newRank = row.rank;
      const movement = oldRank === null || oldRank === undefined
        ? {label:'NEW', tone:'new'}
        : oldRank > newRank
          ? {label:`↑${Math.round(oldRank - newRank)}`, tone:'up'}
          : oldRank < newRank
            ? {label:`↓${Math.round(newRank - oldRank)}`, tone:'down'}
            : {label:'—', tone:''};
      const scoreDelta = old?.score !== null && old?.score !== undefined && row.score !== null
        ? row.score - old.score
        : null;
      return `<div class="compare-rank-row">
        <span>${String(Math.round(newRank)).padStart(2, '0')}</span>
        <div class="compare-rank-main"><b>${esc(row.title)}</b><small>${esc(row.categories.join(' · ') || row.id)}</small></div>
        <em class="compare-position">上次 ${oldRank === null || oldRank === undefined ? '—' : String(Math.round(oldRank)).padStart(2, '0')}</em>
        <em class="compare-score">${scoreDelta === null ? 'Δ —' : `Δ ${signed(scoreDelta, 3)}`}</em>
        <em class="compare-movement ${movement.tone}">${movement.label}</em>
      </div>`;
    }).join('');
    return `<section class="compare-ranking">
      <div class="compare-ranking-head"><div><span class="compare-section-label">RANK MOVEMENT</span><h4>${context.current.domain === 'search' ? '搜索结果位次变化' : '推荐首屏位次变化'}</h4></div><b>本次 / 上次</b></div>
      <div class="compare-rank-table">${rows}</div>
      <div class="compare-rank-foot"><span>只描述相同对象下的可观察排序变化，不把位次上升解释为业务提升。</span><b>${dropped ? `${dropped} 项移出当前 Top 8` : 'Top 8 无移出'}</b></div>
    </section>`;
  }

  function ensureSurface() {
    let node = $('runCompare');
    if (node) return node;
    const anchor = $('strategyExperiment') || $('resultAnalysis') || $('resultSnapshot');
    if (!anchor) return null;
    node = document.createElement('section');
    node.id = 'runCompare';
    node.className = 'run-compare';
    node.hidden = true;
    node.setAttribute('aria-live', 'polite');
    node.setAttribute('tabindex', '-1');
    node.setAttribute('data-run-nav-focus', '');
    anchor.insertAdjacentElement('afterend', node);
    return node;
  }

  function setTriggerState({busy = false, expanded = false} = {}) {
    const button = document.querySelector('.compare-trigger');
    if (!button) return;
    button.disabled = busy;
    button.setAttribute('aria-busy', String(busy));
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = '与上次对照';
  }

  function completedResultVisible() {
    const snapshot = $('resultSnapshot');
    return !!snapshot && !snapshot.hidden && snapshot.textContent.trim().length > 0 && $('stateText')?.textContent.trim() === '已完成';
  }

  function ensureTrigger() {
    triggerQueued = false;
    if (!completedResultVisible()) return;
    const head = document.querySelector('#resultSnapshot .snapshot-head');
    const state = head?.querySelector('.snapshot-state');
    if (!head || !state) return;
    let actions = head.querySelector('.snapshot-actions');
    if (!actions) {
      actions = document.createElement('div');
      actions.className = 'snapshot-actions';
      state.insertAdjacentElement('beforebegin', actions);
      actions.appendChild(state);
    }
    if (!actions.querySelector('.compare-trigger')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'compare-trigger';
      button.textContent = '与上次对照';
      button.setAttribute('aria-controls', 'runCompare');
      button.setAttribute('aria-expanded', 'false');
      button.onclick = () => void compareCurrent();
      actions.appendChild(button);
    }
  }

  function scheduleTrigger() {
    if (triggerQueued) return;
    triggerQueued = true;
    requestAnimationFrame(() => requestAnimationFrame(ensureTrigger));
  }

  function hideCompare({removeTrigger = false} = {}) {
    const node = $('runCompare');
    if (node) {
      node.hidden = true;
      node.innerHTML = '';
    }
    if (removeTrigger) document.querySelector('.compare-trigger')?.remove();
    setTriggerState({expanded:false});
  }

  function invalidate({resetConversation = false} = {}) {
    generation += 1;
    if (resetConversation) currentConversationId = null;
    hideCompare({removeTrigger:true});
  }

  function resolveCurrentConversationId() {
    if (currentConversationId) return currentConversationId;
    const first = document.querySelector('.history-item[data-id]');
    currentConversationId = first?.dataset.id || null;
    return currentConversationId;
  }

  function renderEmpty(message, detail) {
    const node = ensureSurface();
    if (!node) return;
    node.innerHTML = `<div class="run-compare-shell"><div class="compare-empty"><span>RUN COMPARE</span><b>${esc(message)}</b><p>${esc(detail)}</p></div></div>`;
    node.hidden = false;
    setTriggerState({expanded:true});
    node.focus({preventScroll:true});
    node.scrollIntoView({behavior:'smooth', block:'center'});
  }

  function renderComparison(snapshot, baseline) {
    const node = ensureSurface();
    if (!node) return;
    const latest = snapshot.result;
    const previous = baseline.result;
    const latestStats = runStats(latest);
    const previousStats = runStats(previous);
    const latestContext = resultContext(latest);
    const sameTarget = !!baseline.sameTarget;
    const context = {sameTarget, current:latestContext, previous:baseline.context};
    const contextText = sameTarget
      ? `${latestContext.domain === 'search' ? '相同 Query' : '相同用户'} · ${latestContext.label}`
      : `同场景历史 · ${baseline.row?.title || '上一任务'}`;
    const contextDetail = sameTarget
      ? '上下文一致，因此可以展示位次变化；所有 delta 都是两个持久化 run 的直接差值。'
      : '上下文不同，只对照执行与验证事实；不计算排名位次变化，也不推断哪个任务“更好”。';
    node.innerHTML = `<div class="run-compare-shell">
      <div class="compare-head">
        <div><span>RUN COMPARE</span><h3>与最近一次可用历史运行对照</h3></div>
        <div class="compare-head-actions"><strong class="compare-scope ${sameTarget ? 'same' : ''}"><i></i>${sameTarget ? '同对象' : '同场景'}</strong><button type="button" class="compare-close" aria-label="关闭运行对照">×</button></div>
      </div>
      <div class="compare-context ${sameTarget ? '' : 'warn'}"><div><b>${esc(contextText)}</b><small>${esc(contextDetail)}</small></div><time>${esc(formatTime(baseline.timestamp))}</time></div>
      ${verificationHtml(previous, latest)}
      <div class="compare-metrics-head"><span>指标</span><span>上次</span><span>本次</span><span>变化</span></div>
      <div class="compare-metrics">
        ${metricRow('Verifier', previousStats.confidence, latestStats.confidence, 'percent')}
        ${metricRow('执行轮次', previousStats.cycles, latestStats.cycles)}
        ${metricRow('工具调用', previousStats.tools, latestStats.tools)}
        ${metricRow('证据数量', previousStats.evidence, latestStats.evidence)}
        ${metricRow('执行成本', previousStats.cost, latestStats.cost, 'decimal')}
        ${metricRow('Reward', previousStats.reward, latestStats.reward, 'decimal')}
        ${metricRow('Memory hits', previousStats.memory, latestStats.memory)}
      </div>
      ${rankingHtml(previous, latest, context)}
    </div>`;
    node.querySelector('.compare-close').onclick = () => {
      node.hidden = true;
      setTriggerState({expanded:false});
      document.querySelector('.compare-trigger')?.focus();
    };
    node.hidden = false;
    setTriggerState({expanded:true});
    node.focus({preventScroll:true});
    node.scrollIntoView({behavior:'smooth', block:'center'});
  }

  async function compareCurrent() {
    const conversationId = resolveCurrentConversationId();
    if (!conversationId || !completedResultVisible()) return;
    const requestGeneration = generation;
    setTriggerState({busy:true, expanded:false});
    try {
      const conversation = await xhrJson(`/api/conversations/${encodeURIComponent(conversationId)}`);
      const results = assistantResults(conversation);
      const latest = results.at(-1)?.result;
      if (!latest) {
        renderEmpty('当前运行没有可对照结果', '只有完成并持久化的运行会参与历史对照。');
        return;
      }
      const snapshot = {conversation_id:conversationId, scene:String(conversation?.scene || ''), result:latest};
      const baseline = await findBaseline(snapshot, conversation);
      if (requestGeneration !== generation) return;
      if (!baseline) {
        renderEmpty('还没有可用的历史运行', '完成另一个同场景任务后，这里会直接读取持久化结果进行事实对照。');
        return;
      }
      renderComparison(snapshot, baseline);
    } catch (error) {
      if (requestGeneration !== generation) return;
      renderEmpty('历史运行暂时无法读取', error?.message || '稍后可以重新尝试，不影响当前任务结果。');
    } finally {
      if (requestGeneration === generation) setTriggerState({busy:false});
    }
  }

  document.addEventListener('click', event => {
    const history = event.target.closest('.history-item[data-id]');
    if (history) {
      currentConversationId = history.dataset.id || null;
      invalidate();
      return;
    }
    if (event.target.closest('#newTaskBtn, .scene')) {
      invalidate({resetConversation:true});
      return;
    }
    if (event.target.closest('#sendBtn')) invalidate();
  }, true);

  document.addEventListener('keydown', event => {
    if (event.target === $('input') && event.key === 'Enter' && !event.shiftKey && !event.isComposing) invalidate();
  }, true);

  const observer = new MutationObserver(() => {
    if (!completedResultVisible()) {
      if (document.querySelector('.compare-trigger') || !$('runCompare')?.hidden) hideCompare({removeTrigger:true});
      return;
    }
    if (!document.querySelector('.compare-trigger')) scheduleTrigger();
  });
  observer.observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:['hidden']});
  scheduleTrigger();
})();
