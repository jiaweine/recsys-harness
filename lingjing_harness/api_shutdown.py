from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import os
import threading
import time
from typing import Any

from .store_handoff import release_interrupted_run


class WorkerShutdown(BaseException):
    """Internal control signal used only at safe runner action boundaries."""


def _grace_seconds() -> float:
    raw = os.environ.get("LINGJING_SHUTDOWN_GRACE_SECONDS", "25")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("LINGJING_SHUTDOWN_GRACE_SECONDS must be a number") from exc
    return max(1.0, min(value, 120.0))


def guard_runner_for_shutdown(runner: Any, shutdown_event: threading.Event):
    """Inject a worker-shutdown signal without changing user-cancel semantics.

    The original ``should_stop`` callback remains authoritative.  A durable user
    cancel therefore still becomes ``cancelled``.  Worker shutdown is raised as a
    BaseException only after the user stop check returns false, allowing it to
    bypass api_core's ordinary failure/cancel handlers and reach the handoff layer.
    """

    original_run = runner.run

    def guarded_run(*args: Any, **kwargs: Any):
        user_should_stop = kwargs.get("should_stop")

        def should_stop_or_shutdown() -> bool:
            if user_should_stop and user_should_stop():
                return True
            if shutdown_event.is_set():
                raise WorkerShutdown("worker is shutting down")
            return False

        kwargs["should_stop"] = should_stop_or_shutdown
        return original_run(*args, **kwargs)

    runner.run = guarded_run
    return original_run


def _handoff_run(core: Any, run_id: str) -> bool:
    now = time.time()
    with core.RUN_LOCK:
        row = core.RUNS.get(run_id)
        if row is None:
            return False
        events = list(row.get("events") or [])
        progress = int(events[-1].get("progress", 0)) if events else 0
        events.append(
            {
                "phase": "interrupt",
                "title": "执行已安全交接",
                "detail": "worker 正在退出；已保留最近 checkpoint，任务可由其他 worker 继续",
                "progress": progress,
                "payload": {"worker_handoff": True},
                "created_at": now,
            }
        )
        row.update(
            {
                "status": "interrupted",
                "events": events,
                "updated_at": now,
                "owner_id": None,
                "lease_until": None,
            }
        )
        checkpoint = row.get("checkpoint")
        if isinstance(checkpoint, dict):
            checkpoint = copy.deepcopy(checkpoint)
            checkpoint["events"] = copy.deepcopy(events)
            row["checkpoint"] = checkpoint
        snapshot = (
            core._compact_run_snapshot(row)
            if hasattr(core, "_compact_run_snapshot")
            else copy.deepcopy(row)
        )

    released = release_interrupted_run(
        core.store,
        run_id,
        core.WORKER_ID,
        snapshot,
        now=now,
    )
    persisted_status = core.store.run_status(run_id)
    with core.RUN_LOCK:
        row = core.RUNS.get(run_id)
        if row is not None:
            row["status"] = persisted_status or row.get("status", "interrupted")
            if released:
                row["owner_id"] = None
                row["lease_until"] = None
    if released and hasattr(core, "_PERSIST_META"):
        core._PERSIST_META.pop(str(run_id), None)
    return released


def install_shutdown_boundary(core: Any) -> None:
    """Install graceful handoff around the existing stable API implementation."""

    if getattr(core, "_GRACEFUL_SHUTDOWN_INSTALLED", False):
        return

    shutdown_event = threading.Event()
    run_tasks: dict[str, asyncio.Task[Any]] = {}
    grace_seconds = _grace_seconds()
    original_execute = core._execute
    original_lifespan = core.app.router.lifespan_context

    async def interruptible_execute(
        run_id: str,
        cid: str,
        text: str,
        runner: Any,
        **kwargs: Any,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            run_tasks[run_id] = task
        original_run = guard_runner_for_shutdown(runner, shutdown_event)
        try:
            await original_execute(run_id, cid, text, runner, **kwargs)
        except WorkerShutdown:
            _handoff_run(core, run_id)
        finally:
            runner.run = original_run
            if task is not None and run_tasks.get(run_id) is task:
                run_tasks.pop(run_id, None)

    @asynccontextmanager
    async def graceful_lifespan(app: Any):
        shutdown_event.clear()
        # Rebind the public readiness view for every lifespan.  The runner keeps
        # the private event in its closure, while callers outside a live lifespan
        # must never inherit a previous shutdown's sticky state.
        core.SHUTDOWN_EVENT = shutdown_event
        core.SHUTDOWN_PENDING_RUNS = 0
        try:
            async with original_lifespan(app):
                try:
                    yield
                finally:
                    # Keep the original heartbeat alive during the grace window.
                    # Runs that reach a checkpoint hand themselves off immediately;
                    # runs still inside a bounded tool keep their old lease until
                    # the original lifespan exits and heartbeat renewal stops.
                    shutdown_event.set()
                    active = [task for task in set(run_tasks.values()) if not task.done()]
                    if active:
                        _, pending = await asyncio.wait(active, timeout=grace_seconds)
                        core.SHUTDOWN_PENDING_RUNS = len(pending)
        finally:
            # Pending executor runners still close over the old, set event and
            # therefore cannot resume work.  Only the exported readiness view is
            # replaced so a later test/client/lifespan cannot observe stale state.
            core.SHUTDOWN_EVENT = threading.Event()

    core._execute = interruptible_execute
    core.app.router.lifespan_context = graceful_lifespan
    core.SHUTDOWN_EVENT = shutdown_event
    core.RUN_TASKS = run_tasks
    core.SHUTDOWN_GRACE_SECONDS = grace_seconds
    core.SHUTDOWN_PENDING_RUNS = 0
    core._GRACEFUL_SHUTDOWN_INSTALLED = True
