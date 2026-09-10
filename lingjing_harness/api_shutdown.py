from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
import os
import threading
import time
from typing import Any
import uuid

from .api_cancel_execution_fence import install_cancel_execution_fence
from .api_message_start import install_message_start_boundary
from .api_recovery import (
    install_expired_run_recovery_heartbeat,
    install_startup_recovery_batching,
)
from .api_request_body_limit import install_request_body_limit
from .api_security import install_api_security_boundary
from .api_terminal_takeover_fence import install_terminal_takeover_execution_fence
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


def install_run_owner_session(core: Any) -> str:
    """Give this process a unique durable run-owner fencing identity.

    ``LINGJING_WORKER_ID`` is an operator-facing worker label and may be reused
    across a rolling replacement or accidentally shared by two processes.  Run
    leases need a stricter identity: if two live executors present the same
    durable owner value, lease renewal and execution fences cannot distinguish a
    stale executor from its replacement.

    Preserve the configured label separately and make ``WORKER_ID`` the
    process-session fencing token used by the existing run lifecycle call sites.
    The installer is idempotent so repeated stable-API imports cannot rotate an
    active process token underneath its runs.
    """

    existing = str(getattr(core, "RUN_OWNER_ID", "") or "")
    if existing:
        return existing

    label = str(getattr(core, "WORKER_LABEL", "") or getattr(core, "WORKER_ID", "") or "worker")
    owner_id = f"{label}:run:{uuid.uuid4().hex[:12]}"
    core.WORKER_LABEL = label
    core.RUN_OWNER_ID = owner_id
    core.WORKER_ID = owner_id
    return owner_id


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
    """Install late-stage API lifecycle, security, cancellation, and handoff hardening."""

    # Configure the process-session run fencing identity before any lifespan can
    # reserve, recover, renew, persist, execute, or hand off durable runs.  All of
    # those existing paths resolve core.WORKER_ID at runtime, so one installation
    # keeps their owner token coherent without duplicating run-owner plumbing.
    install_run_owner_session(core)

    # Replace the task-start route before request-body middleware captures route
    # ASGI apps. The wrapper reuses the original endpoint and only narrows its
    # conversation existence check, so validation and run lifecycle semantics stay
    # owned by api_core while body limits still cover the final registered route.
    install_message_start_boundary(core)

    # Request-size accounting must sit outside FastAPI's body/model parsing. Add it
    # before the security middleware so the later security layer remains the
    # outermost browser/host boundary while body reads are still bounded beneath it.
    install_request_body_limit(core)

    # This installer is the stable late hook invoked after the API wrapper has
    # replaced persistence/recovery functions and installed all routes.  Keep the
    # browser security, execution fencing, and startup recovery layers idempotent
    # and install them before the graceful-shutdown guard so repeated integration
    # imports cannot silently lose one of the boundaries.
    install_api_security_boundary(core)
    install_terminal_takeover_execution_fence(core)
    install_cancel_execution_fence(core)
    install_startup_recovery_batching(core)
    install_expired_run_recovery_heartbeat(core)

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
