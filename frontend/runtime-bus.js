(() => {
  if (window.XushuRuntimeBus) return;

  const nativeFetch = window.fetch.bind(window);
  const subscribers = new Set();
  const stats = {
    matchedResponses: 0,
    parsedResponses: 0,
    parseErrors: 0,
    dispatches: 0,
  };

  function trackedPath(path) {
    return path.startsWith('/api/runs/') || /^\/api\/conversations\/[^/]+$/.test(path);
  }

  function publish(path, payload) {
    for (const subscriber of subscribers) {
      try {
        subscriber(path, payload);
        stats.dispatches += 1;
      } catch (error) {
        console.error('Runtime response subscriber failed', error);
      }
    }
  }

  const bus = {
    subscribe(subscriber) {
      if (typeof subscriber !== 'function') throw new TypeError('Runtime response subscriber must be a function');
      subscribers.add(subscriber);
      return () => subscribers.delete(subscriber);
    },
    snapshot() {
      return {
        ...stats,
        subscribers: subscribers.size,
      };
    },
  };

  window.XushuRuntimeBus = bus;
  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const rawUrl = typeof input === 'string' ? input : input?.url;
      const path = new URL(rawUrl, location.origin).pathname;
      if (response.ok && trackedPath(path)) {
        stats.matchedResponses += 1;
        response.clone().json().then(payload => {
          stats.parsedResponses += 1;
          publish(path, payload);
        }).catch(() => {
          stats.parseErrors += 1;
        });
      }
    } catch (_) {}
    return response;
  };
})();
