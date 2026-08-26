(() => {
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'
  }[char]));
  let current = {conversation_id:null, scene:'', result:null};
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

  function assistantResult(conversation) {
    const row = [...(conversation?.messages || [])].reverse().find(message => message.role === 'assistant' && message.payload && typeof message.payload === 'object');
    const payload = row?.payload;
    if (!payload || (!Array.isArray(payload.actions) && !payload.verification && !Array.isArray(payload.events))) return null;
    return payload;
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

  async function findBaseline(snapshot) {
    const rows = await xhrJson('/api/conversations');
    if (!Array.isArray(rows)) return null;
    const candidates = rows
      .filter(row => row?.id && row.id !== snapshot.conversation_id && !row.active && row.scene === snapshot.scene)
      .slice(0, 8);
    const target = resultContext(snapshot.result);
    let fallback = null;
    for (const row of candidates) {
      let conversation;
      try { conversation = await xhrJson(`/api/conversations/${encodeURIComponent(row.id)}`); }
      catch { continue; }
      const result = assistantResult(conversation);
      if (!result) continue;
      const context = resultContext(result);
      const candidate = {row, conversation, result, context, sameTarget:false};
      if (!fallback) fallback = candidate;
      if (target.key && context.domain === target.domain && context.key === target.key) {
        candidate.sameTarget = true;
        return candidate;
      }
    }
    return fallback;
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
    return rows.map(row => ({
      id:String(row.id || row.item_id || row.title || ''),
      title:String(row.title || row.id || row.item_id || '结果'),
      rank:numberOrNull(row.rank),
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
    const rows = after.map((row, index) => {
      const old = beforeMap.get(row.id);
      const oldRank = old?.rank;
      const newRank = row.rank ?? index + 1;
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

  function setTriggerState({busy = false, expanded = false, label = null} = {}) {
    const button = document.querySelector('.compare-trigger');
    if (!button) return;
    button.disabled = busy;
    button.setAttribute('aria-busy', String(busy));
    button.setAttribute('aria-expanded', String(expanded));
    if (label) button.textContent = label;
  }

  function ensureTrigger() {
    triggerQueued = false;
    if (!current.result || !current.conversation_id) return;
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
    setTriggerState({expanded:false, label:'与上次对照'});
  }

  function renderEmpty(message, detail) {
    const node = ensureSurface();
    if (!node) return;
    node.innerHTML = `<div class="run-compare-shell"><div class="compare-empty"><span>RUN COMPARE</span><b>${esc(message)}</b><p>${esc(detail)}</p></div></div>`;
    node.hidden = false;
    setTriggerState({expanded:true, label:'重新查找'});
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
    const previousContext = baseline.context;
    const sameTarget = !!baseline.sameTarget;
    const context = {sameTarget, current:latestContext, previous:previousContext};
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
      <div class="compare-context ${sameTarget ? '' : 'warn'}"><div><b>${esc(contextText)}</b><small>${esc(contextDetail)}</small></div><time>${esc(formatTime(baseline.row?.updated_at))}</time></div>
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
      setTriggerState({expanded:false, label:'与上次对照'});
      document.querySelector('.compare-trigger')?.focus();
    };
    node.hidden = false;
    setTriggerState({expanded:true, label:'已对照'});
    node.focus({preventScroll:true});
    node.scrollIntoView({behavior:'smooth', block:'center'});
  }

  async function compareCurrent() {
    if (!current.result || !current.conversation_id) return;
    const requestGeneration = generation;
    const snapshot = {conversation_id:current.conversation_id, scene:current.scene, result:current.result};
    setTriggerState({busy:true, expanded:false, label:'查找上次…'});
    try {
      const baseline = await findBaseline(snapshot);
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

  window.addEventListener('xushu:run-context', event => {
    generation += 1;
    current = {
      conversation_id:event.detail?.conversation_id || null,
      scene:String(event.detail?.scene || ''),
      result:event.detail?.result && typeof event.detail.result === 'object' ? event.detail.result : null,
    };
    hideCompare({removeTrigger:true});
    if (current.result) scheduleTrigger();
  });

  window.addEventListener('xushu:run-start', () => {
    generation += 1;
    current = {...current, result:null};
    hideCompare({removeTrigger:true});
  });

  const observer = new MutationObserver(() => {
    if (current.result && !document.querySelector('.compare-trigger')) scheduleTrigger();
  });
  observer.observe(document.body, {subtree:true, childList:true});
})();
