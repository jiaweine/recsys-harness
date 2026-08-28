const tabs = [...document.querySelectorAll('.tab[role="tab"]')];
const panels = [...document.querySelectorAll('.panel')];

function configureTabs() {
  tabs.forEach((tab, index) => {
    const key = tab.dataset.tab;
    const panel = key ? document.getElementById(`panel-${key}`) : null;
    if (!tab.id) tab.id = `tab-${key || index}`;
    if (panel) {
      tab.setAttribute('aria-controls', panel.id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
    }
  });
}

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

configureTabs();
for (const [index, tab] of tabs.entries()) {
  tab.addEventListener('click', () => queueMicrotask(() => syncTabs(tab)));
  tab.addEventListener('keydown', event => {
    let next = null;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') next = tabs[(index + 1) % tabs.length];
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') next = tabs[(index - 1 + tabs.length) % tabs.length];
    else if (event.key === 'Home') next = tabs[0];
    else if (event.key === 'End') next = tabs.at(-1);
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
if (authForm) {
  authForm.setAttribute('role', 'dialog');
  authForm.setAttribute('aria-modal', 'true');
  const title = authForm.querySelector('h1');
  const description = authForm.querySelector('p');
  if (title) {
    title.id ||= 'authTitle';
    authForm.setAttribute('aria-labelledby', title.id);
  }
  if (description) {
    description.id ||= 'authDescription';
    authForm.setAttribute('aria-describedby', description.id);
  }
}

function authIsOpen() {
  return Boolean(authGate && !authGate.hidden);
}
function syncAuthBoundary() {
  if (shell && 'inert' in shell) shell.inert = authIsOpen();
  if (authIsOpen() && !authForm?.contains(document.activeElement)) {
    authForm?.querySelector('input,button')?.focus({preventScroll: true});
  }
}
function authFocusables() {
  if (!authForm) return [];
  return [...authForm.querySelectorAll('button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])')]
    .filter(element => !element.hidden && element.getClientRects().length > 0);
}
if (authGate) {
  new MutationObserver(syncAuthBoundary).observe(authGate, {attributes: true, attributeFilter: ['hidden']});
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

const messageList = document.getElementById('messageList');
messageList?.setAttribute('role', 'log');
messageList?.setAttribute('aria-relevant', 'additions text');
document.getElementById('running')?.setAttribute('aria-atomic', 'true');

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
    inspector.setAttribute('aria-modal', 'false');
    inspector.setAttribute('aria-hidden', String(!open));
  } else {
    inspector.removeAttribute('role');
    inspector.removeAttribute('aria-modal');
    inspector.removeAttribute('aria-hidden');
  }
  if (drawer && open && !drawerWasOpen) {
    (tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || inspectorClose)?.focus({preventScroll: true});
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
if (inspector) new MutationObserver(syncInspectorBoundary).observe(inspector, {attributes: true, attributeFilter: ['class']});
window.addEventListener('resize', syncInspectorBoundary, {passive: true});
syncInspectorBoundary();
