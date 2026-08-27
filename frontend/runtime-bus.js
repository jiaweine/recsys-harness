(() => {
  if (window.XushuRuntimeBus) return;

  const nativeFetch = window.fetch.bind(window);
  const stats = {
    matchedResponses: 0,
    parsedResponses: 0,
    parseErrors: 0,
    jsonReads: 0,
    cloneReads: 0,
    sourceClones: 0,
    virtualClones: 0,
  };

  function trackedPath(path) {
    return path.startsWith('/api/runs/') || /^\/api\/conversations\/[^/]+$/.test(path);
  }

  function wrapJsonResponse(response, path) {
    if (!response.ok || !trackedPath(path)) return response;

    stats.matchedResponses += 1;
    let payloadPromise;
    try {
      stats.sourceClones += 1;
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

    const virtualClone = () => {
      stats.cloneReads += 1;
      stats.virtualClones += 1;
      return Object.freeze({
        json() {
          stats.jsonReads += 1;
          return payloadPromise;
        },
        clone: virtualClone,
      });
    };

    return new Proxy(response, {
      get(current, property) {
        if (property === 'json') {
          return () => {
            stats.jsonReads += 1;
            return payloadPromise;
          };
        }
        if (property === 'clone') return virtualClone;
        const value = Reflect.get(current, property, current);
        return typeof value === 'function' ? value.bind(current) : value;
      },
    });
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
