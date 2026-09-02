"""Stable Xushu API surface.

The implementation lives in :mod:`lingjing_harness.api_core`.  This entrypoint
keeps the historical ``lingjing_harness.api:app`` import stable and installs
cross-cutting consistency boundaries for workspace identity, durable run reads,
and checkpoint persistence/recovery.
"""

from __future__ import annotations

import asyncio
import copy
import sys
import time
from typing import Any

from fastapi import HTTPException

from . import api_core as _core
from .api_shutdown import install_shutdown_boundary
from .online_experiment_api import install_online_experiment_routes
from .proxy_trust import TRUSTED_PROXY_NETWORKS, client_key as _proxy_client_key
from .rate_limit_maintenance import install_rate_limit_maintenance
from .workspace_identity import workspace_fingerprint


# Agent memory uses a stable strategy-context fingerprint so appended outcomes do
# not erase useful history.  Workspace synchronization is stricter: every worker
# must reload when the production evidence snapshot changes.
_core.catalog_fingerprint = workspace_fingerprint
_core.CATALOG_REVISION = workspace_fingerprint(_core.catalog)

# Rate-limit identity is fail-closed behind proxies.  api_core resolves this
# module global at request time, so replacing it here preserves the stable app
# surface while refusing attacker-controlled X-Forwarded-For from untrusted peers.
_core._client_key = _proxy_client_key
_core.TRUSTED_PROXY_NETWORKS = TRUSTED_PROXY_NETWORKS

# Keep the shared SQLite rate-limit table bounded on a deterministic maintenance
# schedule.  Allowance/counter semantics remain owned by WorkspaceStore.
install_rate_limit_maintenance(_core.store)


# Full run snapshots grow with every action, observation and event.  Persisting
# the whole JSON document for decide + execute + reflect + checkpoint therefore
# creates avoidable SQLite write amplification.  Keep the durable boundaries that
# matter for recovery and cross-worker visibility while coalescing events that are
# immediately followed by a stronger boundary.
_DURABLE_EVENT_PHASES = frozenset({"execute", "resume", "verify", "complete", "cancel"})
_PERSIST_META: dict[str, tuple[Any, ...]] = {}


class _RunLeaseLost(RuntimeError):
    """The durable run lease moved to another worker before a side effect."""


class _LeaseFencedMemory:
    """Fence non-idempotent final learning without changing shared tool memory."""

    def __init__(self, memory: Any, fence) -> None:
        self._memory = memory
        self._fence = fence

    def __getattr__(self, name: str) -> Any:
        return getattr(self._memory, name)

    def update_policy(self, *args: Any, **kwargs: Any) -> Any:
        self._fence()
        return self._memory.update_policy(*args, **kwargs)

    def record_episode(self, *args: Any, **kwargs: Any) -> Any:
        self._fence()
        return self._memory.record_episode(*args, **kwargs)


def _renew_execution_fence(run_id: str) -> None:
    """Linearize execution authority through the existing durable owner lease."""

    if not _core.store.renew_run_lease(
        run_id,
        _core.WORKER_ID,
        _core.RUN_LEASE_SECONDS,
    ):
        raise _RunLeaseLost(f"run lease lost: {run_id}")


def _checkpoint_signature(row: dict[str, Any]) -> tuple[Any, ...] | None:
    checkpoint = row.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return None
    return (
        int(checkpoint.get("cycle", 0) or 0),
        str(checkpoint.get("status") or "running"),
        checkpoint.get("result") is not None,
    )


def _persistence_meta(row: dict[str, Any]) -> tuple[Any, ...]:
    events = row.get("events") or []
    latest_phase = ""
    if events and isinstance(events[-1], dict):
        latest_phase = str(events[-1].get("phase") or "")
    attachments = row.get("attachments") or []
    return (
        str(row.get("status") or "running"),
        len(events),
        latest_phase,
        _checkpoint_signature(row),
        row.get("result") is not None,
        row.get("message") is not None,
        len(attachments) if isinstance(attachments, list) else 0,
        bool(row.get("multimodal_context")),
        bool(row.get("error")),
    )


def _compact_run_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Remove duplicate recovery data without changing the in-memory run.

    Active checkpoints carry the same event list that is already stored at the
    run root.  Keeping both roughly doubles the event portion of every later
    snapshot.  Persist one copy and reconstruct it only when a process actually
    needs to resume.  Terminal rows no longer need a checkpoint at all.
    """

    snapshot = copy.deepcopy(row)
    checkpoint = snapshot.get("checkpoint")
    if isinstance(checkpoint, dict) and checkpoint.get("events") == snapshot.get("events"):
        compact = dict(checkpoint)
        compact.pop("events", None)
        snapshot["checkpoint"] = compact
    if str(snapshot.get("status") or "") not in _core.ACTIVE_RUN_STATUSES:
        snapshot.pop("checkpoint", None)
    return snapshot


def _should_persist_run(row: dict[str, Any]) -> tuple[bool, tuple[Any, ...]]:
    run_id = str(row.get("run_id") or "")
    current = _persistence_meta(row)
    previous = _PERSIST_META.get(run_id)
    if previous is None:
        return True, current

    status, event_count, latest_phase, checkpoint, result_ready, message_ready, attachments, context_ready, error_ready = current
    (
        previous_status,
        previous_event_count,
        _previous_phase,
        previous_checkpoint,
        previous_result_ready,
        previous_message_ready,
        previous_attachments,
        previous_context_ready,
        previous_error_ready,
    ) = previous

    if status != previous_status:
        return True, current
    if checkpoint != previous_checkpoint:
        return True, current
    if result_ready != previous_result_ready or message_ready != previous_message_ready:
        return True, current
    if attachments != previous_attachments or context_ready != previous_context_ready:
        return True, current
    if error_ready != previous_error_ready:
        return True, current
    if event_count != previous_event_count and latest_phase in _DURABLE_EVENT_PHASES:
        return True, current
    return False, current


def _coalesced_persist_run(row: dict[str, Any]) -> None:
    should_persist, current_meta = _should_persist_run(row)
    if not should_persist:
        return

    snapshot = _compact_run_snapshot(row)
    persisted_status = _core.store.save_run(
        row["run_id"],
        row["conversation_id"],
        row.get("goal", ""),
        row.get("status", "running"),
        snapshot,
        owner_id=_core.WORKER_ID,
        lease_seconds=_core.RUN_LEASE_SECONDS,
    )
    if persisted_status != row.get("status"):
        row["status"] = persisted_status
        current_meta = _persistence_meta(row)

    if persisted_status in _core.ACTIVE_RUN_STATUSES:
        run_id = str(row["run_id"])
        # Persistence helpers are also unit-tested as pure snapshot coalescers.
        # Execution authority only applies when this process is actually running
        # the job in its local RUNS set; normal production execution always meets
        # that condition after reserve/claim and before any side effect.
        with _core.RUN_LOCK:
            locally_executing = run_id in _core.RUNS
        if locally_executing:
            try:
                _renew_execution_fence(run_id)
            except _RunLeaseLost:
                _PERSIST_META.pop(run_id, None)
                raise
        _PERSIST_META[run_id] = current_meta
    else:
        _PERSIST_META.pop(str(row["run_id"]), None)


def _inflate_checkpoint(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    checkpoint = snapshot.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return None
    restored = copy.deepcopy(checkpoint)
    if "events" not in restored:
        restored["events"] = copy.deepcopy(snapshot.get("events") or [])
    return restored


async def _recover_on_startup_hardened() -> None:
    """Recover active work without replaying a truly finalized checkpoint.

    Public ``AgentHarness`` now withholds ``status=completed`` until atomic base
    learning plus semantic/mechanism finalization have finished. Legacy completed
    checkpoints may predate that boundary, so recovery idempotently repairs their
    public finalizers before publishing the assistant result.
    """

    claimed = _core.store.claim_recoverable_runs(
        owner_id=_core.WORKER_ID,
        lease_seconds=_core.RUN_LEASE_SECONDS,
        limit=16,
    )
    for saved in claimed:
        run_id = saved["run_id"]
        cid = saved["conversation_id"]
        text = saved["goal"]
        snapshot = saved.get("snapshot") or {}
        snapshot.setdefault("events", [])
        snapshot.setdefault("result", None)
        checkpoint = _inflate_checkpoint(snapshot)

        if saved.get("status") == "cancel_requested":
            snapshot.update(
                {
                    "run_id": run_id,
                    "conversation_id": cid,
                    "goal": text,
                    "status": "cancelled",
                    "updated_at": time.time(),
                }
            )
            snapshot["events"].append(
                {
                    "phase": "cancel",
                    "title": "已停止本次执行",
                    "detail": "服务恢复时确认了停止请求，任务没有重新执行",
                    "progress": int(snapshot["events"][-1].get("progress", 0)) if snapshot["events"] else 0,
                    "payload": {"recovered": True},
                    "created_at": time.time(),
                }
            )
            snapshot.pop("checkpoint", None)
            _core.store.save_run(
                run_id,
                cid,
                text,
                "cancelled",
                snapshot,
                owner_id=_core.WORKER_ID,
            )
            continue

        with _core.WORKSPACE_LOCK:
            current_revision = _core.CATALOG_REVISION
        saved_revision = snapshot.get("catalog_revision") or current_revision
        if saved_revision != current_revision:
            snapshot.update(
                {
                    "run_id": run_id,
                    "conversation_id": cid,
                    "goal": text,
                    "status": "failed",
                    "error": "工作区数据已变化，未恢复旧数据任务",
                    "updated_at": time.time(),
                }
            )
            snapshot.pop("checkpoint", None)
            _core.store.save_run(
                run_id,
                cid,
                text,
                "failed",
                snapshot,
                owner_id=_core.WORKER_ID,
            )
            continue

        if (
            isinstance(checkpoint, dict)
            and checkpoint.get("status") == "completed"
            and isinstance(checkpoint.get("result"), dict)
        ):
            result = copy.deepcopy(checkpoint["result"])
            _renew_execution_fence(run_id)
            finalizer = getattr(_core.harness, "finalize_result", None)
            if callable(finalizer):
                result = finalizer(result)
            result["job_id"] = run_id
            result["attachments"] = copy.deepcopy(snapshot.get("attachments") or [])
            result["catalog_revision"] = saved_revision
            existing = _core.store.assistant_for_job(cid, run_id)
            if existing is None:
                message = _core.store.add_message(cid, "assistant", str(result.get("answer") or ""), result)
            else:
                message = existing
                result = copy.deepcopy(existing.get("payload") or result)
            snapshot.update(
                {
                    "run_id": run_id,
                    "conversation_id": cid,
                    "goal": text,
                    "status": "completed",
                    "result": result,
                    "message": message,
                    "catalog_revision": saved_revision,
                    "updated_at": time.time(),
                }
            )
            snapshot.pop("checkpoint", None)
            _core.store.save_run(
                run_id,
                cid,
                text,
                "completed",
                snapshot,
                owner_id=_core.WORKER_ID,
            )
            continue

        snapshot.update(
            {
                "run_id": run_id,
                "conversation_id": cid,
                "goal": text,
                "status": "running",
                "catalog_revision": saved_revision,
                "updated_at": time.time(),
            }
        )
        with _core.RUN_LOCK:
            _core.RUNS[run_id] = snapshot
            _core._persist_run(snapshot)
        asyncio.create_task(
            _core._execute(
                run_id,
                cid,
                text,
                _core.harness.fork(),
                attachment_ids=list(snapshot.get("attachment_ids") or []),
                allow_network=bool(snapshot.get("allow_network")),
                resume=checkpoint,
                catalog_revision=saved_revision,
            )
        )


_ORIGINAL_EXECUTE = _core._execute


async def _execute_with_run_lease_fence(
    run_id: str,
    cid: str,
    text: str,
    runner: Any,
    *,
    attachment_ids: list[str] | None = None,
    allow_network: bool = False,
    resume: dict[str, Any] | None = None,
    catalog_revision: str | None = None,
) -> None:
    """Fence tool, learning, and assistant side effects to the current lease owner."""

    original_memory = runner.memory
    original_run = runner.run
    had_finalization_key = hasattr(runner, "_finalization_event_key")
    original_finalization_key = getattr(runner, "_finalization_event_key", None)
    runner._finalization_event_key = f"api:{run_id}"
    runner.memory = _LeaseFencedMemory(
        original_memory,
        lambda: _renew_execution_fence(run_id),
    )

    def run_with_final_fence(*args: Any, **kwargs: Any) -> Any:
        result = original_run(*args, **kwargs)
        _renew_execution_fence(run_id)
        return result

    runner.run = run_with_final_fence
    try:
        await _ORIGINAL_EXECUTE(
            run_id,
            cid,
            text,
            runner,
            attachment_ids=attachment_ids,
            allow_network=allow_network,
            resume=resume,
            catalog_revision=catalog_revision,
        )
    except _RunLeaseLost:
        with _core.RUN_LOCK:
            _core.RUNS.pop(run_id, None)
        _PERSIST_META.pop(run_id, None)
    finally:
        runner.run = original_run
        runner.memory = original_memory
        if had_finalization_key:
            runner._finalization_event_key = original_finalization_key
        else:
            try:
                delattr(runner, "_finalization_event_key")
            except AttributeError:
                pass


# Install persistence and recovery boundaries before the application lifespan is
# entered.  api_core resolves these globals at runtime, so existing route and
# integration monkeypatch behavior stays intact.
_core._persist_run = _coalesced_persist_run
_core._recover_on_startup = _recover_on_startup_hardened
_core._execute = _execute_with_run_lease_fence
_core._compact_run_snapshot = _compact_run_snapshot
_core._inflate_checkpoint = _inflate_checkpoint
_core._PERSIST_META = _PERSIST_META
_core._RunLeaseLost = _RunLeaseLost


_RUN_SNAPSHOT_RETRIES = 3


def _snapshot_in_memory_run(run_id: str) -> dict[str, Any] | None:
    """Copy one run without letting a transient nested-mutation race escape.

    The top-level run map is protected by ``RUN_LOCK``, but callback payloads can
    briefly retain runner-owned nested dictionaries while a checkpoint is being
    handed across the thread boundary.  ``deepcopy`` raises ``RuntimeError`` if
    such a dictionary changes size during iteration.  Retry the in-memory hot
    path a bounded number of times, then fall back to the durable snapshot rather
    than turning a polling request into a 500 response.
    """

    for attempt in range(_RUN_SNAPSHOT_RETRIES):
        with _core.RUN_LOCK:
            row = _core.RUNS.get(run_id)
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
        return _core.store.get_run(run_id)
    except KeyError as exc:
        raise HTTPException(404, "执行任务不存在") from exc


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

    snapshot = _snapshot_in_memory_run(run_id)

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


def _health_live() -> dict[str, str]:
    """Process liveness probe with no dependency on durable state."""

    return {"status": "ok"}


def _health_ready() -> dict[str, str]:
    """Converge safe workspace state, then fail closed if serving is not ready."""

    shutdown_event = getattr(_core, "SHUTDOWN_EVENT", None)
    if shutdown_event is not None and shutdown_event.is_set():
        raise HTTPException(503, "worker is shutting down")
    try:
        if not _core._sync_workspace():
            raise HTTPException(503, "workspace revision not ready")
        durable_revision = _core.store.workspace_revision()
        updating = _core.store.workspace_update_active()
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "durable store unavailable") from exc

    with _core.WORKSPACE_LOCK:
        local_revision = str(_core.CATALOG_REVISION or "")

    if not durable_revision or durable_revision != local_revision:
        raise HTTPException(503, "workspace revision not ready")
    if updating:
        raise HTTPException(503, "workspace update in progress")
    return {"status": "ready"}


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
_core.app.add_api_route("/health/live", _health_live, methods=["GET"], name="health_live")
_core.app.add_api_route("/health/ready", _health_ready, methods=["GET"], name="health_ready")
_core.get_run = _coherent_get_run
_core.health_live = _health_live
_core.health_ready = _health_ready

# Online experiments share the durable workspace database, hardened identity and
# rate-limit counter, but they never call serving/production write APIs.  The
# returned registry is exposed for diagnostics/tests after the stable module alias
# is installed below.
_core.online_experiment_store = install_online_experiment_routes(
    _core.app,
    database_path=_core.DATA / "workspace.db",
    rate_limiter=_core.store.consume_rate_limit,
    client_key=lambda request: _core._client_key(request),
)

# Install shutdown handoff after persistence/recovery wrappers are in place so
# interrupted snapshots use the same compaction and fencing semantics.
install_shutdown_boundary(_core)

# Preserve the exact module object expected by integrations that import and
# monkeypatch internals from ``lingjing_harness.api``.
sys.modules[__name__] = _core
