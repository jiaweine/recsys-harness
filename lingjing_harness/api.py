"""Stable Xushu API surface.

The implementation lives in :mod:`lingjing_harness.api_core`.  This entrypoint
keeps the historical ``lingjing_harness.api:app`` import stable and installs two
small cross-cutting consistency boundaries: full production-evidence workspace
revisioning and coherent terminal run reads.
"""

from __future__ import annotations

import copy
import sys

from fastapi import HTTPException

from . import api_core as _core
from .workspace_identity import workspace_fingerprint


# Agent memory uses a stable strategy-context fingerprint so appended outcomes do
# not erase useful history.  Workspace synchronization is stricter: every worker
# must reload when the production evidence snapshot changes.
_core.catalog_fingerprint = workspace_fingerprint
_core.CATALOG_REVISION = workspace_fingerprint(_core.catalog)


def _coherent_get_run(run_id: str):
    """Never expose a terminal status with an older in-memory payload.

    Active runs are polled frequently by the UI.  Reading and decoding the whole
    durable snapshot on every poll makes that hot path scale with checkpoint
    size, even when persistence is still in the same active state.  Read the
    lightweight durable status first and fetch the full snapshot only when the
    durable run has crossed a terminal boundary.

    This still preserves the consistency invariant that motivated this wrapper:
    a caller must never observe ``completed`` (or another terminal status) with
    an older in-memory payload such as ``result=None``.
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
        persisted_status = _core.store.run_status(run_id)
        if persisted_status is None:
            raise HTTPException(404, "执行任务不存在")

        if persisted_status not in _core.ACTIVE_RUN_STATUSES:
            try:
                persisted = _core.store.get_run(run_id)
            except KeyError as exc:
                raise HTTPException(404, "执行任务不存在") from exc
            snapshot = persisted
            with _core.RUN_LOCK:
                current = _core.RUNS.get(run_id)
                if current is not None and current.get("status") in _core.ACTIVE_RUN_STATUSES:
                    current.clear()
                    current.update(copy.deepcopy(persisted))
        else:
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
