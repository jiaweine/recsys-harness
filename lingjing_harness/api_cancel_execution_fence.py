from __future__ import annotations

from typing import Any


class _CancelFencedMemory:
    """Refuse learning side effects once a durable stop request has won."""

    def __init__(self, memory: Any, check_cancel) -> None:
        self._memory = memory
        self._check_cancel = check_cancel

    def __getattr__(self, name: str) -> Any:
        return getattr(self._memory, name)

    def update_policy(self, *args: Any, **kwargs: Any) -> Any:
        self._check_cancel()
        return self._memory.update_policy(*args, **kwargs)

    def record_episode(self, *args: Any, **kwargs: Any) -> Any:
        self._check_cancel()
        return self._memory.record_episode(*args, **kwargs)


def install_cancel_execution_fence(core: Any) -> None:
    """Linearize durable user cancellation at side-effect boundaries.

    ``should_stop`` remains the cheap cooperative path between runner actions, but
    a remote cancel can land after that poll and before the next tool, learning,
    or assistant-publish side effect.  ``save_run`` already preserves a durable
    ``cancel_requested`` row against later running/interrupted checkpoints; this
    boundary turns that durable state into ``RunCancelled`` before the caller is
    allowed to proceed.

    Cancellation is deliberately separate from lease ownership.  Heartbeats may
    keep a cancel-requested run leased while its current bounded tool finishes;
    only a new side-effect boundary is refused.  A cancel that lands after a
    boundary has linearized therefore stops after the already-authorized current
    action, matching the public stop semantics.
    """

    if getattr(core, "_CANCEL_EXECUTION_FENCE_INSTALLED", False):
        return

    original_persist = core._persist_run
    original_execute = core._execute

    def cancel_requested(run_id: str) -> bool:
        return core.store.run_status(run_id) == "cancel_requested"

    def raise_if_cancel_requested(run_id: str) -> None:
        if cancel_requested(run_id):
            raise core.RunCancelled(f"run cancel requested: {run_id}")

    def persist_with_cancel_fence(row: dict[str, Any]) -> None:
        run_id = str(row.get("run_id") or "")
        requested_status = str(row.get("status") or "running")
        original_persist(row)

        # Only convert a durable cancel into runner control flow for a task that
        # is actually executing.  Startup recovery briefly places claimed rows in
        # RUNS before scheduling them; RUN_TASKS distinguishes that staging state
        # from a live executor and avoids turning a recovery race into a poison-row
        # failure.  The normal RunCancelled handler will atomically finalize the
        # durable row as cancelled and append the public cancel event.
        run_tasks = getattr(core, "RUN_TASKS", None)
        executing = isinstance(run_tasks, dict) and run_id in run_tasks
        if (
            executing
            and requested_status in core.ACTIVE_RUN_STATUSES
            and str(row.get("status") or "") == "cancel_requested"
        ):
            raise core.RunCancelled(f"run cancel requested: {run_id}")

    async def execute_with_cancel_fence(
        run_id: str,
        cid: str,
        text: str,
        runner: Any,
        **kwargs: Any,
    ) -> None:
        original_memory = runner.memory
        original_run = runner.run

        runner.memory = _CancelFencedMemory(
            original_memory,
            lambda: raise_if_cancel_requested(run_id),
        )

        def run_with_completion_cancel_fence(*args: Any, **run_kwargs: Any) -> Any:
            result = original_run(*args, **run_kwargs)
            # Runner completion is the last boundary before the API publishes the
            # assistant message.  A cancel already durable here wins and is
            # finalized by api_core's existing RunCancelled handler.
            raise_if_cancel_requested(run_id)
            return result

        runner.run = run_with_completion_cancel_fence
        try:
            await original_execute(run_id, cid, text, runner, **kwargs)
        finally:
            runner.run = original_run
            runner.memory = original_memory

    core._persist_run = persist_with_cancel_fence
    core._execute = execute_with_cancel_fence
    core._CANCEL_EXECUTION_FENCE_INSTALLED = True


__all__ = ["install_cancel_execution_fence"]
