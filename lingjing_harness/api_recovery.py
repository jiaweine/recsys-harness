from __future__ import annotations

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
            core.store.claim_recoverable_runs = original_claim

    core._recover_on_startup = recover_without_batch_starvation
    core._STARTUP_RECOVERY_BATCHING_INSTALLED = True
