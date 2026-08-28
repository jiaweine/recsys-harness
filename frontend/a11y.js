const install = () => {
  const tabs = [...document.querySelectorAll('.tab[role="tab"]')];
  const panels = [...document.querySelectorAll('.panel')];

  const panelFor = tab => {
    const key = tab?.dataset?.tab;
    return key ? document.getElementById(`panel-${key}`) : null;
  };

  function syncTabs(preferred) {
    const selected = preferred || tabs.find(tab => tab.classList.contains('active')) || tabs[0];
    if (!selected) return;
    const selectedPanel = panelFor(selected);
    tabs.forEach(tab => {
      const active = tab === selected;
      tab.tabIndex = active ? 0 : -1;
      tab.setAttribute('aria-selected', String(active));
    });
    panels.forEach(panel => {
      panel.setAttribute('aria-hidden', String(panel !== selectedPanel));
    });
  }

  tabs.forEach((tab, index) => {
    const panel = panelFor(tab);
    const key = tab.dataset.tab || String(index + 1);
    tab.id ||= `tab-${key}`;
    if (panel) {
      tab.setAttribute('aria-controls', panel.id);
      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('aria-labelledby', tab.id);
    }
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
  });
  syncTabs();

  const messages = document.getElementById('messageList');
  if (messages) {
    messages.setAttribute('role', 'log');
    messages.setAttribute('aria-live', 'polite');
    messages.setAttribute('aria-relevant', 'additions text');
  }
  document.getElementById('running')?.setAttribute('aria-atomic', 'true');

  const authGate = document.getElementById('authGate');
  const authForm = document.getElementById('authForm');
  const shell = document.querySelector('.shell');
  if (authForm) {
    const title = authForm.querySelector('h1');
    const description = authForm.querySelector('p');
    if (title) title.id ||= 'authTitle';
    if (description) description.id ||= 'authDescription';
    authForm.setAttribute('role', 'dialog');
    authForm.setAttribute('aria-modal', 'true');
    if (title) authForm.setAttribute('aria-labelledby', title.id);
    if (description) authForm.setAttribute('aria-describedby', description.id);
  }

  const authIsOpen = () => Boolean(authGate && !authGate.hidden);
  const visibleFocusables = root => root ? [...root.querySelectorAll(
    'button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),a[href],[tabindex]:not([tabindex="-1"])'
  )].filter(element => !element.hidden && element.getClientRects().length > 0) : [];

  function syncAuthBoundary() {
    const open = authIsOpen();
    if (shell && 'inert' in shell) shell.inert = open;
    if (shell) {
      if (open) shell.setAttribute('aria-hidden', 'true');
      else shell.removeAttribute('aria-hidden');
    }
  }

  if (authGate) {
    new MutationObserver(syncAuthBoundary).observe(authGate, {
      attributes: true,
      attributeFilter: ['hidden'],
    });
    syncAuthBoundary();
  }

  const inspector = document.getElementById('inspector');
  const inspectorToggle = document.getElementById('inspectorToggle');
  const inspectorClose = document.getElementById('inspectorClose');
  const inspectorTitle = inspector?.querySelector('.inspector-head b');
  if (inspectorTitle) inspectorTitle.id ||= 'inspectorTitle';
  let inspectorReturnFocus = null;
  let drawerWasOpen = false;

  const inspectorIsDrawer = () => Boolean(
    inspector && inspectorToggle && getComputedStyle(inspectorToggle).display !== 'none'
  );
  const inspectorIsOpen = () => inspectorIsDrawer() && inspector?.classList.contains('open');

  function syncInspectorBoundary() {
    if (!inspector) return;
    const drawer = inspectorIsDrawer();
    const open = !drawer || inspector.classList.contains('open');
    if (drawer) {
      inspector.setAttribute('role', 'dialog');
      inspector.setAttribute('aria-modal', 'true');
      if (inspectorTitle) inspector.setAttribute('aria-labelledby', inspectorTitle.id);
      inspector.setAttribute('aria-hidden', String(!open));
    } else {
      inspector.removeAttribute('role');
      inspector.removeAttribute('aria-modal');
      inspector.removeAttribute('aria-labelledby');
      inspector.removeAttribute('aria-hidden');
    }

    if (drawer && open && !drawerWasOpen) {
      const selected = tabs.find(tab => tab.getAttribute('aria-selected') === 'true') || tabs[0] || inspectorClose;
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

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && inspectorIsOpen() && !authIsOpen()) {
      event.preventDefault();
      inspectorClose?.click();
      return;
    }
    if (event.key !== 'Tab') return;

    if (authIsOpen()) {
      const focusables = visibleFocusables(authForm);
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
      return;
    }

    if (inspectorIsOpen()) {
      const focusables = visibleFocusables(inspector);
      if (!focusables.length) return;
      const first = focusables[0];
      const last = focusables.at(-1);
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !inspector.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !inspector.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    }
  });
};

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', install, {once: true});
} else {
  install();
}
