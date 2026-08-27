(() => {
  const STORAGE_KEY = 'xushu-theme';
  const THEMES = new Set(['light', 'dark']);
  const root = document.documentElement;

  function storedTheme() {
    try {
      const value = localStorage.getItem(STORAGE_KEY);
      return THEMES.has(value) ? value : null;
    } catch {
      return null;
    }
  }

  function updateChrome(theme) {
    const themeColor = document.querySelector('meta[name="theme-color"]');
    if (themeColor) themeColor.setAttribute('content', theme === 'dark' ? '#09090b' : '#f6f7f9');
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      const active = button.dataset.themeChoice === theme;
      button.setAttribute('aria-pressed', String(active));
      button.classList.toggle('active', active);
    });
  }

  function applyTheme(theme, persist = false) {
    const next = THEMES.has(theme) ? theme : 'light';
    root.dataset.theme = next;
    updateChrome(next);
    if (persist) {
      try { localStorage.setItem(STORAGE_KEY, next); } catch {}
    }
    window.dispatchEvent(new CustomEvent('xushu:theme-change', {detail:{theme:next}}));
  }

  // Run before styles paint. The markup itself also defaults to light for no-JS fallback.
  applyTheme(storedTheme() || root.dataset.theme || 'light');

  document.addEventListener('DOMContentLoaded', () => {
    updateChrome(root.dataset.theme || 'light');
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.addEventListener('click', () => applyTheme(button.dataset.themeChoice, true));
    });
  });
})();
