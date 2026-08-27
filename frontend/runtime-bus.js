(() => {
  if (window.XushuRuntimeBus) return;

  const nativeFetch = window.fetch.bind(window);
  const stats = {
    matchedResponses: 0,
    parsedResponses: 0,
    parseErrors: 0,
    jsonReads: 0,
    cloneReads: 0,
  };

  function trackedPath(path) {
    return path.startsWith('/api/runs/') || /^\/api\/conversations\/[^/]+$/.test(path);
  }

  function wrapJsonResponse(response, path) {
    if (!response.ok || !trackedPath(path)) return response;

    stats.matchedResponses += 1;
    let payloadPromise;
    try {
      payloadPromise = response.clone().json().then(payload => {
        stats.parsedResponses += 1;
        return payload;
      }).catch(error => {
        stats.parseErrors += 1;
        throw error;
      });
    } catch (_) {
      stats.parseErrors += 1;
      return response;
    }

    const wrap = target => new Proxy(target, {
      get(current, property) {
        if (property === 'json') {
          return () => {
            stats.jsonReads += 1;
            return payloadPromise;
          };
        }
        if (property === 'clone') {
          return () => {
            stats.cloneReads += 1;
            return wrap(current.clone());
          };
        }
        const value = Reflect.get(current, property, current);
        return typeof value === 'function' ? value.bind(current) : value;
      },
    });

    return wrap(response);
  }

  window.XushuRuntimeBus = {
    snapshot() {
      return {...stats};
    },
  };

  window.fetch = async (...args) => {
    const response = await nativeFetch(...args);
    try {
      const input = args[0];
      const rawUrl = typeof input === 'string' ? input : input?.url;
      const path = new URL(rawUrl, location.origin).pathname;
      return wrapJsonResponse(response, path);
    } catch (_) {
      return response;
    }
  };
})();
