"""Stable Xushu API surface.

The implementation lives in :mod:`lingjing_harness.api_core`.  This entrypoint
keeps the historical ``lingjing_harness.api:app`` import stable and installs the
run-read coherence boundary in one small, reviewable place.
"""

from __future__ import annotations

import copy
import sys

from fastapi import HTTPException

from . import api_core as _core


def _coherent_get_run(run_id: str):
    """Never expose a terminal status with an older in-memory payload.

    A poll can copy a local ``running`` snapshot immediately before the owner
    finishes and persists ``completed``.  Reading only the persisted status at
    that point creates an impossible response: ``completed`` + ``result=None``.
    If persistence has crossed a terminal boundary, return the *whole* durable
    snapshot and refresh the local cache instead of splicing just the status.
    """

    with _core.RUN_LOCK:
        row = _core.RUNS.get(run_id)
        snapshot = copy.deepcopy(row) if row is not None else None

    if snapshot is None:
        try:
            return _core.store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "执行任务不存在") from exc

    if snapshot.get("status") in _core.ACTIVE_RUN_STATUSES:
        try:
            persisted = _core.store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "执行任务不存在") from exc
        persisted_status = str(persisted.get("status") or "")
        if persisted_status and persisted_status not in _core.ACTIVE_RUN_STATUSES:
            snapshot = persisted
            with _core.RUN_LOCK:
                current = _core.RUNS.get(run_id)
                if current is not None and current.get("status") in _core.ACTIVE_RUN_STATUSES:
                    current.clear()
                    current.update(copy.deepcopy(persisted))
        elif persisted_status:
            snapshot["status"] = persisted_status
    return snapshot


# Replace only the original GET route.  All other route functions and module
# globals stay owned by api_core so existing tests/integrations can still patch
# AgentHarness, store, perception and recovery hooks through lingjing_harness.api.
for _route in list(_core.app.router.routes):
    if (
        getattr(_route, "path", None) == "/api/runs/{run_id}"
        and "GET" in (getattr(_route, "methods", None) or set())
    ):
        _core.app.router.routes.remove(_route)

_core.app.add_api_route(
    "/api/runs/{run_id}",
    _coherent_get_run,
    methods=["GET"],
    name="get_run",
)
_core.get_run = _coherent_get_run

# Preserve the exact module object expected by integrations that import and
# monkeypatch internals from ``lingjing_harness.api``.
sys.modules[__name__] = _core
