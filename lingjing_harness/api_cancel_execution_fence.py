from __future__ import annotations

import threading
from typing import Any

from .store_run_completion import install_run_completion_publication_fence


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
    only a new side-effect boundary is refused.  Assistant publication is the one
    boundary that must also atomically terminalize the durable run so a successful
    stop request can never linearize between publication and completion.
    """

    if getattr(core, "_CANCEL_EXECUTION_FENCE_INSTALLED", False):
        return

    install_run_completion_publication_fence(core)

    original_persist = core._persist_run
    original_execute = core._execute
    runner_context = threading.local()

    def cancel_requested(run_id: str) -> bool:
        return core.store.run_status(run_id) == "cancel_requested"

    def raise_if_cancel_requested(run_id: str) -> None:
        if cancel_requested(run_id):
            raise core.RunCancelled(f"run cancel requested: {run_id}")

    def persist_with_cancel_fence(row: dict[str, Any]) -> None:
        run_id = str(row.get("run_id") or "")
        requested_status = str(row.get("status") or "running")
        original_persist(row)

        # Persisting attachments/perception also happens while the asyncio run
        # task exists, but before api_core enters its RunCancelled handler.  Fence
        # only persistence invoked from inside runner.run itself.  The thread-local
        # marker is set in the executor thread around the runner call, so startup
        # recovery staging and pre-run perception remain ordinary cooperative
        # cancellation paths rather than unhandled control-flow exceptions.
        executing_here = getattr(runner_context, "run_id", None) == run_id
        if (
            executing_here
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
            previous_run_id = getattr(runner_context, "run_id", None)
            runner_context.run_id = run_id
            try:
                result = original_run(*args, **run_kwargs)
                # Catch a cancel already durable when the runner returns.  The
                # later assistant publication transaction performs the final
                # cancel-vs-complete linearization immediately before the message
                # can become visible.
                raise_if_cancel_requested(run_id)
                return result
            finally:
                if previous_run_id is None:
                    try:
                        del runner_context.run_id
                    except AttributeError:
                        pass
                else:
                    runner_context.run_id = previous_run_id

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
