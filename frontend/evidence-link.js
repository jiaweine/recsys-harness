(() => {
  const normalize = value => String(value || '').trim().replace(/\s+/g, ' ');
  const highlightTimers = new WeakMap();
  let queued = false;

  function evidenceItems() {
    return [...document.querySelectorAll('#evidenceList .evidence-item')];
  }

  function rankRows() {
    return [...document.querySelectorAll('#resultAnalysis .rank-row')];
  }

  function rankIdentity(row) {
    const title = normalize(row.dataset.evidenceTitle || row.querySelector('.rank-title > b')?.textContent);
    const rank = Number(row.dataset.evidenceRank || normalize(row.querySelector('.rank-index')?.textContent));
    return {title, rank:Number.isFinite(rank) && rank > 0 ? rank : null};
  }

  function evidenceIdentity(item) {
    const title = normalize(item.querySelector('b')?.textContent);
    const detail = normalize(item.querySelector('small')?.textContent);
    const match = detail.match(/第\s*(\d+)\s*位/);
    const rank = match ? Number(match[1]) : null;
    return {title, detail, rank:Number.isFinite(rank) && rank > 0 ? rank : null};
  }

  function rankContext(row) {
    const block = row?.closest('.result-analysis-block');
    const eyebrow = normalize(block?.querySelector('.analysis-head span')?.textContent).toUpperCase();
    const subjectText = normalize(block?.querySelector('.analysis-head > b')?.textContent);
    const subject = subjectText && subjectText !== '当前任务' ? subjectText : '';
    const domain = eyebrow.includes('SEARCH') ? 'search' : eyebrow.includes('RECOMMENDATION') ? 'recommend' : '';
    return {domain, subject};
  }

  function findEvidence(title, rank) {
    const wanted = normalize(title);
    if (!wanted) return null;
    const matches = evidenceItems().filter(item => normalize(item.querySelector('b')?.textContent) === wanted);
    const position = Number(rank);
    if (Number.isFinite(position) && position > 0) {
      const positioned = matches.find(item => normalize(item.querySelector('small')?.textContent).includes(`第 ${position} 位`));
      if (positioned) return positioned;
    }
    return matches.length === 1 ? matches[0] : null;
  }

  function findRank(title, rank) {
    const wanted = normalize(title);
    if (!wanted) return null;
    const matches = rankRows().filter(row => rankIdentity(row).title === wanted);
    const position = Number(rank);
    if (Number.isFinite(position) && position > 0) {
      const positioned = matches.find(row => rankIdentity(row).rank === position);
      if (positioned) return positioned;
    }
    return matches.length === 1 ? matches[0] : null;
  }

  function selectEvidenceTab() {
    const toggle = document.getElementById('inspectorToggle');
    if (toggle && toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
    document.querySelector('.tab[data-tab="evidence"]')?.click();
  }

  function focusCanBeReclaimed(active) {
    if (!active || active === document.body) return true;
    return active.id === 'inspectorToggle'
      || active.id === 'inspectorClose'
      || active.classList?.contains('rank-evidence-link')
      || active.classList?.contains('evidence-rank-link')
      || active.classList?.contains('evidence-followup-link')
      || active.classList?.contains('tab');
  }

  function settleEvidenceFocus(item, attempt = 0) {
    if (!item.isConnected || document.activeElement === item) return;
    if (attempt > 0 && !focusCanBeReclaimed(document.activeElement)) return;
    item.focus({preventScroll:true});
    if (document.activeElement === item || attempt >= 4) return;
    setTimeout(() => settleEvidenceFocus(item, attempt + 1), 45 + attempt * 25);
  }

  function settleComposerFocus(input, attempt = 0) {
    if (!input?.isConnected || document.activeElement === input) return;
    if (attempt > 0 && !focusCanBeReclaimed(document.activeElement)) return;
    input.focus({preventScroll:true});
    input.setSelectionRange(input.value.length, input.value.length);
    if (document.activeElement === input || attempt >= 5) return;
    setTimeout(() => settleComposerFocus(input, attempt + 1), 55 + attempt * 35);
  }

  function highlightEvidence(item) {
    const prior = highlightTimers.get(item);
    if (prior) clearTimeout(prior);
    document.querySelectorAll('.evidence-item.evidence-target').forEach(node => {
      if (node !== item) node.classList.remove('evidence-target');
    });
    item.classList.remove('evidence-target');
    item.offsetWidth;
    item.classList.add('evidence-target');
    item.tabIndex = -1;
    item.scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'center',
    });
    requestAnimationFrame(() => settleEvidenceFocus(item));
    highlightTimers.set(item, setTimeout(() => item.classList.remove('evidence-target'), 1800));
  }

  function highlightRank(row) {
    const prior = highlightTimers.get(row);
    if (prior) clearTimeout(prior);
    document.querySelectorAll('.rank-row.rank-target').forEach(node => {
      if (node !== row) node.classList.remove('rank-target');
    });
    row.classList.remove('rank-target');
    row.offsetWidth;
    row.classList.add('rank-target');
    row.tabIndex = -1;
    row.scrollIntoView({
      behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
      block: 'center',
    });
    requestAnimationFrame(() => row.focus({preventScroll:true}));
    highlightTimers.set(row, setTimeout(() => row.classList.remove('rank-target'), 1800));
  }

  function openEvidence(title, rank) {
    selectEvidenceTab();
    requestAnimationFrame(() => requestAnimationFrame(() => {
      const item = findEvidence(title, rank);
      if (item) highlightEvidence(item);
    }));
  }

  function openRank(title, rank) {
    const row = findRank(title, rank);
    if (!row) return;
    const inspector = document.getElementById('inspector');
    const close = document.getElementById('inspectorClose');
    const mobileSheet = matchMedia('(max-width: 720px)').matches;
    if (mobileSheet && inspector?.classList.contains('open') && close) close.click();
    requestAnimationFrame(() => requestAnimationFrame(() => highlightRank(row)));
  }

  function compactDetail(value) {
    const detail = normalize(value);
    if (detail.length <= 180) return detail;
    return `${detail.slice(0, 177)}…`;
  }

  function followupDraft(item, row) {
    const {title, rank, detail} = evidenceIdentity(item);
    const {domain, subject} = rankContext(row);
    const position = rank ? `（当前第 ${rank} 位）` : '';
    const evidence = compactDetail(detail);
    const evidenceClause = evidence ? `本次已有依据是：${evidence}。` : '';
    let scope = '当前结果';
    if (domain === 'search') scope = subject ? `搜索“${subject}”` : '当前搜索结果';
    if (domain === 'recommend') scope = subject ? `用户 ${subject} 的推荐首屏` : '当前推荐首屏';
    return `继续检查${scope}中的「${title}」${position}。${evidenceClause}重点核对它为什么出现在这个位置，优先补充可复现证据和诊断；先不要改变当前策略。`;
  }

  function prepareFollowup(item) {
    const {title, rank} = evidenceIdentity(item);
    const row = findRank(title, rank);
    const input = document.getElementById('input');
    if (!row || !input) return;

    const draft = followupDraft(item, row);
    const existing = input.value.trim();
    input.value = existing ? `${input.value.replace(/\s+$/, '')}\n\n${draft}` : draft;
    input.dispatchEvent(new Event('input', {bubbles:true}));
    input.style.height = 'auto';
    input.style.height = `${Math.min(150, Math.max(58, input.scrollHeight))}px`;

    const inspector = document.getElementById('inspector');
    const close = document.getElementById('inspectorClose');
    const mobileSheet = matchMedia('(max-width: 720px)').matches;
    const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (mobileSheet && inspector?.classList.contains('open') && close) close.click();

    const focusComposer = () => {
      document.getElementById('composerWrap')?.scrollIntoView({
        behavior: reducedMotion ? 'auto' : 'smooth',
        block: 'end',
      });
      requestAnimationFrame(() => settleComposerFocus(input));
    };
    if (mobileSheet && !reducedMotion) setTimeout(focusComposer, 300);
    else requestAnimationFrame(focusComposer);
  }

  function decorateRow(row) {
    if (row.dataset.evidenceLinked === 'true') return;
    const {title, rank} = rankIdentity(row);
    if (!title) return;

    row.dataset.evidenceLinked = 'true';
    row.dataset.evidenceTitle = title;
    if (rank) row.dataset.evidenceRank = String(rank);

    const titleWrap = row.querySelector('.rank-title');
    if (titleWrap && !titleWrap.querySelector('.rank-evidence-link')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'rank-evidence-link';
      button.textContent = '依据';
      button.setAttribute('aria-label', `查看 ${title} 的判断依据`);
      titleWrap.appendChild(button);
    }
  }

  function decorateEvidence(item) {
    const {title, rank} = evidenceIdentity(item);
    if (!title) return;
    const row = findRank(title, rank);
    if (!row) return;

    item.dataset.resultLinked = 'true';
    item.dataset.followupLinked = 'true';
    item.dataset.resultTitle = title;
    if (rank) item.dataset.resultRank = String(rank);

    if (!item.querySelector('.evidence-rank-link')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'evidence-rank-link';
      button.textContent = '结果';
      button.setAttribute('aria-label', `回到 ${title} 的排名结果`);
      item.appendChild(button);
    }
    if (!item.querySelector('.evidence-followup-link')) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'evidence-followup-link';
      button.textContent = '检查';
      button.setAttribute('aria-label', `基于 ${title} 的当前证据继续检查`);
      item.appendChild(button);
    }
  }

  function decorate() {
    queued = false;
    rankRows().forEach(decorateRow);
    evidenceItems().forEach(decorateEvidence);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(decorate);
  }

  document.addEventListener('click', event => {
    const evidenceButton = event.target.closest('.rank-evidence-link');
    if (evidenceButton) {
      const row = evidenceButton.closest('#resultAnalysis .rank-row[data-evidence-linked="true"]');
      if (row) openEvidence(row.dataset.evidenceTitle, row.dataset.evidenceRank);
      return;
    }

    const followupButton = event.target.closest('.evidence-followup-link');
    if (followupButton) {
      const item = followupButton.closest('#evidenceList .evidence-item[data-followup-linked="true"]');
      if (item) prepareFollowup(item);
      return;
    }

    const rankButton = event.target.closest('.evidence-rank-link');
    if (!rankButton) return;
    const item = rankButton.closest('#evidenceList .evidence-item[data-result-linked="true"]');
    if (!item) return;
    openRank(item.dataset.resultTitle, item.dataset.resultRank);
  });

  const observer = new MutationObserver(mutations => {
    if (mutations.some(mutation => [...mutation.addedNodes].some(node =>
      node.nodeType === 1 && (
        node.matches?.('.rank-row, #resultAnalysis, .evidence-item, #evidenceList')
        || node.querySelector?.('.rank-row, .evidence-item')
      )
    ))) schedule();
  });
  observer.observe(document.body, {subtree:true, childList:true});
  decorate();
})();
