from __future__ import annotations

import copy
import json
import time
from typing import Any

from .store import WorkspaceStore


def release_interrupted_run(
    store: WorkspaceStore,
    run_id: str,
    owner_id: str,
    snapshot: dict[str, Any],
    *,
    now: float | None = None,
) -> bool:
    """Atomically release an owned active run for immediate recovery.

    Graceful shutdown may only hand a run off after its runner has stopped at an
    action/checkpoint boundary.  The update is fenced by both owner identity and
    active status so a stale worker cannot release work that another worker has
    already claimed or completed.  An explicit durable cancel request always wins
    over shutdown handoff and is therefore never rewritten as ``interrupted``.
    """

    now = time.time() if now is None else float(now)
    payload = copy.deepcopy(snapshot)
    payload["status"] = "interrupted"
    payload["updated_at"] = now

    # WorkspaceStore intentionally serializes cross-worker mutations with
    # BEGIN IMMEDIATE.  This helper lives next to the store rather than reaching
    # through the API layer so handoff uses the same transaction/fencing model.
    with store._lock, store._connect() as connection:  # noqa: SLF001 - package-internal boundary
        connection.execute("begin immediate")
        row = connection.execute(
            "select status,owner_id from runs where run_id=?",
            (run_id,),
        ).fetchone()
        if (
            not row
            or str(row["status"]) not in {"running", "interrupted"}
            or str(row["owner_id"] or "") != owner_id
        ):
            connection.rollback()
            return False

        cursor = connection.execute(
            """
            update runs
            set status='interrupted',snapshot=?,updated_at=?,owner_id=null,lease_until=null
            where run_id=? and owner_id=? and status in ('running','interrupted')
            """,
            (json.dumps(payload, ensure_ascii=False), now, run_id, owner_id),
        )
        if cursor.rowcount != 1:
            connection.rollback()
            return False
        connection.commit()
    return True
