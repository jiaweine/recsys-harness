from __future__ import annotations

import copy
import time
from typing import Any

from fastapi import HTTPException


_RUN_SNAPSHOT_RETRIES = 3


def install_terminal_takeover_execution_fence(core: Any) -> None:
    """Keep a terminal successor from disarming a stale local executor fence.

    ``WorkspaceStore.save_run`` deliberately accepts an existing terminal row as
    authoritative and returns its status when a stale writer arrives later.  That
    is useful for read/state convergence, but an active local executor must treat
    the transition as lost authority: the successor has already finished the run.
    Otherwise the local row becomes terminal, the normal active-status renewal
    fence is skipped, and the stale executor can enter its next tool side effect.

    The run GET path has the same subtle boundary.  It may return the successor's
    durable terminal snapshot to clients, but it must not overwrite an active
    local executor row with that terminal snapshot.  Keeping the local row active
    lets the next durable execution boundary observe the terminal takeover and
    retire the stale executor before side effects.

    A final lease fence can also lose after ``runner.run`` returns.  ``api_core``
    catches ordinary exceptions around the runner and may then converge only the
    local status through a failed persistence attempt.  Once the executor has
    fully returned, no side-effect fence remains to protect, so reconcile any
    semantically different terminal local payload with the durable terminal row.
    """

    if getattr(core, "_TERMINAL_TAKEOVER_EXECUTION_FENCE_INSTALLED", False):
        return

    original_persist = core._persist_run
    original_execute = core._execute

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
            # api_core catches ordinary Exception values around runner.run() so a
            # _RunLeaseLost raised from an event sink would otherwise be converted
            # into its generic failure path before the outer execution-fence layer
            # can retire the run.  Once durable terminal authority is observed at
            # this side-effect boundary, remove the stale local executor first;
            # the raised control signal then unwinds the runner and the generic
            # handler finds no local row to mutate or persist.
            with core.RUN_LOCK:
                core.RUNS.pop(run_id, None)
            persist_meta = getattr(core, "_PERSIST_META", None)
            if isinstance(persist_meta, dict):
                persist_meta.pop(run_id, None)
            raise core._RunLeaseLost(f"run lease lost after terminal takeover: {run_id}")

    @staticmethod
    def terminal_payload_signature(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            str(row.get("status") or ""),
            row.get("result"),
            row.get("message"),
            row.get("error"),
        )

    async def execute_with_terminal_convergence(
        run_id: str,
        cid: str,
        text: str,
        runner: Any,
        **kwargs: Any,
    ) -> None:
        await original_execute(run_id, cid, text, runner, **kwargs)

        # It is unsafe to rewrite an active local row while its executor may still
        # enter another fenced side-effect boundary.  This wrapper runs only after
        # the wrapped executor has returned, so a terminal durable row is now the
        # canonical cache payload rather than an execution-authority signal.
        with core.RUN_LOCK:
            local = core.RUNS.get(run_id)
            if local is None or local.get("status") in core.ACTIVE_RUN_STATUSES:
                return
            local_signature = terminal_payload_signature(local)

        try:
            durable = core.store.get_run(run_id)
        except KeyError:
            return
        if durable.get("status") in core.ACTIVE_RUN_STATUSES:
            return
        if terminal_payload_signature(durable) == local_signature:
            return

        with core.RUN_LOCK:
            current = core.RUNS.get(run_id)
            if current is None or current.get("status") in core.ACTIVE_RUN_STATUSES:
                return
            current.clear()
            current.update(copy.deepcopy(durable))
        persist_meta = getattr(core, "_PERSIST_META", None)
        if isinstance(persist_meta, dict):
            persist_meta.pop(run_id, None)

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
                    # leave the active local executor row untouched.  Read-side
                    # cache convergence cannot safely prove there is no executor
                    # about to enter its next fenced side-effect boundary.
                    return core.store.get_run(run_id)
                except KeyError as exc:
                    raise HTTPException(404, "执行任务不存在") from exc
            snapshot["status"] = persisted_status
        return snapshot

    core._persist_run = persist_with_terminal_takeover_fence
    core._execute = execute_with_terminal_convergence

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
