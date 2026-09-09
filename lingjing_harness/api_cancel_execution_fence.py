from __future__ import annotations

import threading
import time
from typing import Any

from .store_run_completion import install_run_completion_publication_fence


PERCEPTION_DURABLE_CANCEL_POLL_SECONDS = 0.5


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

    ``should_stop`` remains the cooperative path between runner actions, but a
    remote cancel can land after that poll and before the next tool, learning, or
    assistant-publish side effect. ``save_run`` already preserves a durable
    ``cancel_requested`` row against later running/interrupted checkpoints; this
    boundary turns that durable state into ``RunCancelled`` before the caller is
    allowed to proceed.

    Perception is different from a side-effect boundary: api_core waits on one
    bounded perception task and polls the same durable stop callback every 100ms.
    That callback opens a fresh SQLite connection, so an 18-second perception can
    otherwise create roughly 180 status reads while no cancellation is happening.
    Bound only that waiting-loop poll to twice per second. Runner, learning,
    persistence, and publication fences keep their full durable checks unchanged.

    Cancellation is deliberately separate from lease ownership. Heartbeats may
    keep a cancel-requested run leased while its current bounded tool finishes;
    only a new side-effect boundary is refused. Assistant publication is the one
    boundary that must also atomically terminalize the durable run so a successful
    stop request can never linearize between publication and completion.
    """

    if getattr(core, "_CANCEL_EXECUTION_FENCE_INSTALLED", False):
        return

    install_run_completion_publication_fence(core)

    original_persist = core._persist_run
    original_execute = core._execute
    original_perceive = core._perceive_with_cancel
    runner_context = threading.local()

    def cancel_requested(run_id: str) -> bool:
        return core.store.run_status(run_id) == "cancel_requested"

    def raise_if_cancel_requested(run_id: str) -> None:
        if cancel_requested(run_id):
            raise core.RunCancelled(f"run cancel requested: {run_id}")

    async def perceive_with_bounded_cancel_poll(
        rows: list[dict[str, Any]],
        should_stop,
    ) -> tuple[str, list[dict[str, Any]]]:
        last_checked = float("-inf")
        stopped = False
        poll_lock = threading.Lock()

        def bounded_should_stop() -> bool:
            nonlocal last_checked, stopped
            if stopped:
                return True
            now = time.monotonic()
            with poll_lock:
                if stopped:
                    return True
                if now - last_checked < PERCEPTION_DURABLE_CANCEL_POLL_SECONDS:
                    return False
                last_checked = now
                stopped = bool(should_stop())
                return stopped

        result = await original_perceive(rows, bounded_should_stop)

        # Do not let a cancel that landed between the last bounded poll and
        # perception completion leak a newly observed context into the next phase.
        # This final exact check also keeps short perception tasks cancellation-safe.
        if stopped or should_stop():
            return "", [
                core._public_attachment(row, perception_status="cancelled")
                for row in rows
            ]
        return result

    def persist_with_cancel_fence(row: dict[str, Any]) -> None:
        run_id = str(row.get("run_id") or "")
        requested_status = str(row.get("status") or "running")
        original_persist(row)

        # Persisting attachments/perception also happens while the asyncio run
        # task exists, but before api_core enters its RunCancelled handler. Fence
        # only persistence invoked from inside runner.run itself. The thread-local
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
                # Catch a cancel already durable when the runner returns. The
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

    core._perceive_with_cancel = perceive_with_bounded_cancel_poll
    core._persist_run = persist_with_cancel_fence
    core._execute = execute_with_cancel_fence
    core._CANCEL_EXECUTION_FENCE_INSTALLED = True


__all__ = [
    "PERCEPTION_DURABLE_CANCEL_POLL_SECONDS",
    "install_cancel_execution_fence",
]
