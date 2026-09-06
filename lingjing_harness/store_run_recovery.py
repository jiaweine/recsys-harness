from __future__ import annotations

import time
from typing import Any


def install_run_recovery_claim_fence(store_module: Any) -> None:
    """Make durable run recovery a takeover operation, never owner re-entry.

    A run owner is now a process-session fencing token.  Recovery therefore must
    not treat an exact owner match as permission to claim an unexpired run: doing
    so lets a second recovery pass duplicate work that the same live process is
    already executing.  Only ownerless, lease-less, or genuinely expired active
    rows are recoverable.

    Keep the existing transaction, future-clock repair, ordering, snapshot shape,
    and lease-duration semantics unchanged.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_RUN_RECOVERY_CLAIM_FENCE_INSTALLED", False):
        return

    def claim_recoverable_runs(
        self,
        *,
        owner_id: str,
        lease_seconds: float,
        limit: int = 20,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        now = time.time() if now is None else float(now)
        lease_until = now + max(1.0, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            self._repair_future_run_leases(connection, now)
            rows = connection.execute(
                """
                select run_id,conversation_id,goal,status,snapshot
                from runs
                where status in ('running','interrupted','cancel_requested')
                  and (owner_id is null or lease_until is null or lease_until<?)
                order by updated_at desc
                limit ?
                """,
                (now, limit),
            ).fetchall()
            claimed = []
            for row in rows:
                cursor = connection.execute(
                    """
                    update runs set owner_id=?,lease_until=?
                    where run_id=?
                      and status in ('running','interrupted','cancel_requested')
                      and (owner_id is null or lease_until is null or lease_until<?)
                    """,
                    (owner_id, lease_until, row["run_id"], now),
                )
                if cursor.rowcount != 1:
                    continue
                claimed.append(
                    {
                        "run_id": row["run_id"],
                        "conversation_id": row["conversation_id"],
                        "goal": row["goal"],
                        "status": row["status"],
                        "snapshot": self._loads(row["snapshot"]),
                    }
                )
            connection.commit()
        return claimed

    cls.claim_recoverable_runs = claim_recoverable_runs
    cls._RUN_RECOVERY_CLAIM_FENCE_INSTALLED = True


__all__ = ["install_run_recovery_claim_fence"]
