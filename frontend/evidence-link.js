(() => {
  const normalize = value => String(value || '').trim().replace(/\s+/g, ' ');
  const highlightTimers = new WeakMap();
  let queued = false;

  function evidenceItems() {
    return [...document.querySelectorAll('#evidenceList .evidence-item')];
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

  function selectEvidenceTab() {
    const toggle = document.getElementById('inspectorToggle');
    if (toggle && toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
    document.querySelector('.tab[data-tab="evidence"]')?.click();
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
    item.focus({preventScroll:true});
    highlightTimers.set(item, setTimeout(() => item.classList.remove('evidence-target'), 1800));
  }

  function openEvidence(title, rank) {
    selectEvidenceTab();
    requestAnimationFrame(() => {
      const item = findEvidence(title, rank);
      if (item) highlightEvidence(item);
    });
  }

  function decorateRow(row) {
    if (row.dataset.evidenceLinked === 'true') return;
    const titleNode = row.querySelector('.rank-title > b');
    const title = normalize(titleNode?.textContent);
    const rank = Number(normalize(row.querySelector('.rank-index')?.textContent));
    if (!title) return;

    row.dataset.evidenceLinked = 'true';
    row.dataset.evidenceTitle = title;
    if (Number.isFinite(rank) && rank > 0) row.dataset.evidenceRank = String(rank);

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

  function decorate() {
    queued = false;
    document.querySelectorAll('#resultAnalysis .rank-row').forEach(decorateRow);
  }

  function schedule() {
    if (queued) return;
    queued = true;
    requestAnimationFrame(decorate);
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('.rank-evidence-link');
    if (!button) return;
    const row = button.closest('#resultAnalysis .rank-row[data-evidence-linked="true"]');
    if (!row) return;
    openEvidence(row.dataset.evidenceTitle, row.dataset.evidenceRank);
  });

  const observer = new MutationObserver(mutations => {
    if (mutations.some(mutation => [...mutation.addedNodes].some(node =>
      node.nodeType === 1 && (node.matches?.('.rank-row, #resultAnalysis') || node.querySelector?.('.rank-row'))
    ))) schedule();
  });
  observer.observe(document.body, {subtree:true, childList:true});
  decorate();
})();
