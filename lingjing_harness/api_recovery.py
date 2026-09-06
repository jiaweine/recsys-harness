from __future__ import annotations

import asyncio
import time
from typing import Any


def install_startup_recovery_batching(core: Any) -> None:
    """Make one startup recovery pass cover every currently claimable run.

    ``WorkspaceStore.claim_recoverable_runs`` deliberately accepts a bounded
    ``limit``.  The API recovery layer historically called it once with 16, so a
    busy durable store could leave older recoverable runs untouched forever if
    every restart kept seeing the same newer cohort first.

    Preserve the store contract and fencing semantics: repeatedly ask for a
    larger prefix at one anchored clock value, de-duplicate by run id, then hand
    the complete unique snapshot to the existing hardened recovery function once.
    No extra worker, table, state machine, or execution authority is introduced.
    """

    if getattr(core, "_STARTUP_RECOVERY_BATCHING_INSTALLED", False):
        return

    original_recover = core._recover_on_startup
    original_claim = core.store.claim_recoverable_runs
    store_had_instance_claim = "claim_recoverable_runs" in vars(core.store)
    original_instance_claim = vars(core.store).get("claim_recoverable_runs")

    async def recover_without_batch_starvation() -> None:
        def claim_all_currently_recoverable(
            *,
            owner_id: str,
            lease_seconds: float,
            limit: int = 20,
            now: float | None = None,
        ) -> list[dict[str, Any]]:
            anchored_now = time.time() if now is None else float(now)
            request_limit = max(1, int(limit))
            unique: dict[str, dict[str, Any]] = {}

            while True:
                rows = original_claim(
                    owner_id=owner_id,
                    lease_seconds=lease_seconds,
                    limit=request_limit,
                    now=anchored_now,
                )
                for row in rows:
                    run_id = str(row.get("run_id") or "")
                    if run_id and run_id not in unique:
                        unique[run_id] = row
                if len(rows) < request_limit:
                    break
                request_limit *= 2

            return list(unique.values())

        core.store.claim_recoverable_runs = claim_all_currently_recoverable
        try:
            await original_recover()
        finally:
            if store_had_instance_claim:
                core.store.claim_recoverable_runs = original_instance_claim
            else:
                delattr(core.store, "claim_recoverable_runs")

    core._recover_on_startup = recover_without_batch_starvation
    core._STARTUP_RECOVERY_BATCHING_INSTALLED = True


async def run_lease_heartbeat_iteration(core: Any) -> None:
    """Renew local execution leases, then recover newly claimable durable runs."""

    with core.RUN_LOCK:
        active_ids = [
            run_id
            for run_id, row in core.RUNS.items()
            if row.get("status") in core.ACTIVE_RUN_STATUSES
        ]
    for run_id in active_ids:
        core.store.renew_run_lease(
            run_id,
            core.WORKER_ID,
            core.RUN_LEASE_SECONDS,
        )

    # Recovery claims are non-reentrant at the store boundary.  Calling the
    # hardened startup recovery repeatedly therefore cannot duplicate this
    # process's own live runs, while ownerless or expired peer runs become
    # recoverable as soon as a heartbeat observes them.
    await core._recover_on_startup()


def install_expired_run_recovery_heartbeat(core: Any) -> None:
    """Reuse the existing lease heartbeat as the expired-run recovery cadence."""

    if getattr(core, "_EXPIRED_RUN_RECOVERY_HEARTBEAT_INSTALLED", False):
        return

    async def lease_heartbeat_with_recovery() -> None:
        interval = max(1.0, core.RUN_LEASE_SECONDS / 3.0)
        while True:
            await asyncio.sleep(interval)
            await run_lease_heartbeat_iteration(core)

    core._lease_heartbeat_loop = lease_heartbeat_with_recovery
    core._EXPIRED_RUN_RECOVERY_HEARTBEAT_INSTALLED = True


__all__ = [
    "install_expired_run_recovery_heartbeat",
    "install_startup_recovery_batching",
    "run_lease_heartbeat_iteration",
]
