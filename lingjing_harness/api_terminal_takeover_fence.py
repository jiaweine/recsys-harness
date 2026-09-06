from __future__ import annotations

import copy
import time
from typing import Any

from fastapi import HTTPException


_RUN_SNAPSHOT_RETRIES = 3


def install_terminal_takeover_execution_fence(core: Any) -> None:
    """Keep a terminal successor from disarming a stale local executor fence.

    ``WorkspaceStore.save_run`` deliberately returns the durable status when an
    attempted write loses a race.  That is useful for read/state convergence, but
    an active local executor must treat a durable terminal status as lost
    authority: the successor that owns the lease has already finished the run.
    Otherwise the local row becomes terminal, the normal active-status renewal
    fence is skipped, and the stale executor can enter its next tool side effect.

    The run GET path has the same subtle boundary.  It may return the successor's
    durable terminal snapshot to clients, but it must not overwrite an active
    local executor row with that terminal snapshot.  Keeping the local row active
    lets the next durable execution boundary observe the terminal takeover and
    retire the stale executor before side effects.
    """

    if getattr(core, "_TERMINAL_TAKEOVER_EXECUTION_FENCE_INSTALLED", False):
        return

    original_persist = core._persist_run

    def persist_with_terminal_takeover_fence(row: dict[str, Any]) -> None:
        run_id = str(row.get("run_id") or "")
        requested_status = str(row.get("status") or "running")
        with core.RUN_LOCK:
            locally_executing = run_id in core.RUNS

        original_persist(row)

        persisted_status = str(row.get("status") or "")
        if (
            locally_executing
            and requested_status in core.ACTIVE_RUN_STATUSES
            and persisted_status not in core.ACTIVE_RUN_STATUSES
        ):
            persist_meta = getattr(core, "_PERSIST_META", None)
            if isinstance(persist_meta, dict):
                persist_meta.pop(run_id, None)
            raise core._RunLeaseLost(f"run lease lost after terminal takeover: {run_id}")

    def snapshot_in_memory_run(run_id: str) -> dict[str, Any] | None:
        for attempt in range(_RUN_SNAPSHOT_RETRIES):
            with core.RUN_LOCK:
                row = core.RUNS.get(run_id)
                if row is None:
                    return None
                try:
                    return copy.deepcopy(row)
                except RuntimeError as exc:
                    if "dictionary changed size during iteration" not in str(exc):
                        raise
            if attempt + 1 < _RUN_SNAPSHOT_RETRIES:
                time.sleep(0)

        try:
            return core.store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(404, "执行任务不存在") from exc

    def coherent_get_run_without_disarming_fence(run_id: str):
        snapshot = snapshot_in_memory_run(run_id)

        if snapshot is None:
            try:
                return core.store.get_run(run_id)
            except KeyError as exc:
                raise HTTPException(404, "执行任务不存在") from exc

        if snapshot.get("status") in core.ACTIVE_RUN_STATUSES:
            persisted_status = core.store.run_status(run_id)
            if persisted_status is None:
                raise HTTPException(404, "执行任务不存在")
            if persisted_status not in core.ACTIVE_RUN_STATUSES:
                try:
                    # Return the authoritative terminal payload to the reader, but
                    # leave the active local executor row untouched.  Its next
                    # persist/execute boundary must still be able to fail closed.
                    return core.store.get_run(run_id)
                except KeyError as exc:
                    raise HTTPException(404, "执行任务不存在") from exc
            snapshot["status"] = persisted_status
        return snapshot

    core._persist_run = persist_with_terminal_takeover_fence

    for route in list(core.app.router.routes):
        if (
            getattr(route, "path", None) == "/api/runs/{run_id}"
            and "GET" in (getattr(route, "methods", None) or set())
        ):
            core.app.router.routes.remove(route)

    core.app.add_api_route(
        "/api/runs/{run_id}",
        coherent_get_run_without_disarming_fence,
        methods=["GET"],
        name="get_run",
    )
    core.get_run = coherent_get_run_without_disarming_fence
    core._TERMINAL_TAKEOVER_EXECUTION_FENCE_INSTALLED = True


__all__ = ["install_terminal_takeover_execution_fence"]
