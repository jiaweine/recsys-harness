from __future__ import annotations

import asyncio
import logging
import time
from typing import Any


_LOGGER = logging.getLogger(__name__)


def install_startup_recovery_batching(core: Any) -> None:
    """Recover every claimable run without letting one bad row poison the batch.

    ``WorkspaceStore.claim_recoverable_runs`` deliberately accepts a bounded
    ``limit``.  Recovery historically claimed many rows in one transaction and
    then processed them serially.  If the first claimed run raised while being
    restored, every later row was already leased to this worker but never got a
    chance to run.  Repeating the sweep could hit the same poison row again after
    lease expiry and starve healthy work indefinitely.

    Preserve the durable claim transaction and hardened per-run recovery path,
    but expose at most one row to each invocation of that path.  A failed row is
    left durably fenced until its lease expires, while the next claim can proceed
    to other recoverable work immediately.  Claim/infrastructure failures still
    propagate because no row was successfully isolated in that case.
    """

    if getattr(core, "_STARTUP_RECOVERY_BATCHING_INSTALLED", False):
        return

    original_recover = core._recover_on_startup
    original_claim = core.store.claim_recoverable_runs
    store_had_instance_claim = "claim_recoverable_runs" in vars(core.store)
    original_instance_claim = vars(core.store).get("claim_recoverable_runs")

    async def recover_without_batch_starvation() -> None:
        anchored_now = time.time()
        claimed_this_attempt: list[dict[str, Any]] = []
        claim_returned = False

        def claim_one_currently_recoverable(
            *,
            owner_id: str,
            lease_seconds: float,
            limit: int = 20,
            now: float | None = None,
        ) -> list[dict[str, Any]]:
            nonlocal claimed_this_attempt, claim_returned
            rows = original_claim(
                owner_id=owner_id,
                lease_seconds=lease_seconds,
                limit=1,
                now=anchored_now if now is None else float(now),
            )
            claimed_this_attempt = list(rows)
            claim_returned = True
            return rows

        def discard_failed_local_recovery(run_id: str) -> None:
            runs = getattr(core, "RUNS", None)
            run_lock = getattr(core, "RUN_LOCK", None)
            if isinstance(runs, dict) and run_lock is not None:
                with run_lock:
                    runs.pop(run_id, None)
            persist_meta = getattr(core, "_PERSIST_META", None)
            if isinstance(persist_meta, dict):
                persist_meta.pop(run_id, None)

        core.store.claim_recoverable_runs = claim_one_currently_recoverable
        try:
            while True:
                claimed_this_attempt = []
                claim_returned = False
                try:
                    await original_recover()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # If claim itself failed (or recovery raised before/after an
                    # empty claim), this is not an isolated poison row.  Keep the
                    # startup/infrastructure failure visible to the caller.
                    if not claim_returned or not claimed_this_attempt:
                        raise
                    failed_run_id = str(claimed_this_attempt[0].get("run_id") or "")
                    discard_failed_local_recovery(failed_run_id)
                    _LOGGER.exception(
                        "durable run recovery failed for %s; continuing with remaining work",
                        failed_run_id,
                    )
                    continue

                # Some tests and alternate recovery implementations intentionally
                # do no durable claiming.  Preserve their single-call behavior.
                if not claim_returned or not claimed_this_attempt:
                    break
        finally:
            if store_had_instance_claim:
                core.store.claim_recoverable_runs = original_instance_claim
            else:
                delattr(core.store, "claim_recoverable_runs")

    core._recover_on_startup = recover_without_batch_starvation
    core._STARTUP_RECOVERY_BATCHING_INSTALLED = True


async def run_lease_heartbeat_iteration(core: Any) -> bool:
    """Renew local leases, then best-effort recover newly claimable durable runs.

    Recovery is deliberately fail-soft here.  A malformed checkpoint, transient
    storage failure, or lease race while recovering peer work must not terminate
    the coroutine that keeps this process's already-running jobs leased.  Direct
    startup recovery still propagates infrastructure errors normally; isolated
    per-run failures are logged by the batching boundary.  Task cancellation
    remains authoritative and is never swallowed.
    """

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
    try:
        await core._recover_on_startup()
    except asyncio.CancelledError:
        raise
    except Exception:
        _LOGGER.exception("expired run recovery sweep failed")
        return False
    return True


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
