const tabs = [...document.querySelectorAll('.tab[role="tab"]')];
const panels = [...document.querySelectorAll('.panel[role="tabpanel"]')];

function syncTabs(preferred) {
  const selected = preferred || tabs.find(tab => tab.classList.contains('active')) || tabs[0];
  if (!selected) return;
  const panelId = selected.getAttribute('aria-controls');
  tabs.forEach(tab => {
    const active = tab === selected;
    tab.tabIndex = active ? 0 : -1;
    tab.setAttribute('aria-selected', String(active));
  });
  panels.forEach(panel => {
    panel.setAttribute('aria-hidden', String(panel.id !== panelId));
  });
}

for (const [index, tab] of tabs.entries()) {
  tab.addEventListener('click', () => queueMicrotask(() => syncTabs(tab)));
  tab.addEventListener('keydown', event => {
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
      next = tabs[(index + 1) % tabs.length];
    } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
      next = tabs[(index - 1 + tabs.length) % tabs.length];
    } else if (event.key === 'Home') {
      next = tabs[0];
    } else if (event.key === 'End') {
      next = tabs.at(-1);
    }
    if (!next) return;
    event.preventDefault();
    next.click();
    next.focus({preventScroll: true});
  });
}
syncTabs();

const authGate = document.getElementById('authGate');
const authForm = document.getElementById('authForm');
const shell = document.querySelector('.shell');

function authIsOpen() {
  return Boolean(authGate && !authGate.hidden);
}

function syncAuthBoundary() {
  if (shell && 'inert' in shell) shell.inert = authIsOpen();
}

function authFocusables() {
  if (!authForm) return [];
  return [...authForm.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])')]
    .filter(element => !element.hidden && element.getClientRects().length > 0);
}

if (authGate) {
  new MutationObserver(syncAuthBoundary).observe(authGate, {
    attributes: true,
    attributeFilter: ['hidden'],
  });
  syncAuthBoundary();
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Tab' || !authIsOpen()) return;
  const focusables = authFocusables();
  if (!focusables.length) return;
  const first = focusables[0];
  const last = focusables.at(-1);
  const active = document.activeElement;
  if (event.shiftKey && (active === first || !authForm.contains(active))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (active === last || !authForm.contains(active))) {
    event.preventDefault();
    first.focus();
  }
});

const inspector = document.getElementById('inspector');
const inspectorToggle = document.getElementById('inspectorToggle');
const inspectorClose = document.getElementById('inspectorClose');
let inspectorReturnFocus = null;
let drawerWasOpen = false;

function inspectorIsDrawer() {
  return Boolean(inspectorToggle && getComputedStyle(inspectorToggle).display !== 'none');
}

function syncInspectorBoundary() {
  if (!inspector) return;
  const drawer = inspectorIsDrawer();
  const open = !drawer || inspector.classList.contains('open');

  if (drawer) {
    inspector.setAttribute('role', 'dialog');
    inspector.setAttribute('aria-labelledby', 'inspectorTitle');
    inspector.setAttribute('aria-hidden', String(!open));
  } else {
    inspector.removeAttribute('role');
    inspector.removeAttribute('aria-labelledby');
    inspector.removeAttribute('aria-hidden');
  }

  if (drawer && open && !drawerWasOpen) {
    const selected = tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || tabs[0];
    selected?.focus({preventScroll: true});
  } else if (drawer && !open && drawerWasOpen && inspectorReturnFocus?.isConnected) {
    inspectorReturnFocus.focus({preventScroll: true});
  }
  drawerWasOpen = drawer && open;
}

inspectorToggle?.addEventListener('click', () => {
  inspectorReturnFocus = document.activeElement;
  queueMicrotask(syncInspectorBoundary);
});
inspectorClose?.addEventListener('click', () => queueMicrotask(syncInspectorBoundary));
if (inspector) {
  new MutationObserver(syncInspectorBoundary).observe(inspector, {
    attributes: true,
    attributeFilter: ['class'],
  });
}
window.addEventListener('resize', syncInspectorBoundary, {passive: true});
syncInspectorBoundary();
