from __future__ import annotations

import sqlite3
from typing import Any


def _storage_and_run_id(memory: Any, run_id: str) -> tuple[Any, str]:
    """Resolve one effective runtime facade to the SQLite owner and stored run id."""

    scoped = getattr(memory, "_scoped_invocation_id", None)
    base_memory = getattr(memory, "base_memory", None)
    if callable(scoped) and base_memory is not None:
        return base_memory, str(scoped(run_id))
    return memory, str(run_id)


def discard_completed_run_invocations(memory: Any, run_id: str) -> int:
    """Best-effort removal of replay rows after one run is durably complete.

    ``agent_skill_events`` exists only to make adaptive-tool writes idempotent if
    execution fails before a checkpoint records the completed action. Once the
    harness has returned successfully (and therefore the final checkpoint sink has
    also returned successfully when configured), those full tool-result replay rows
    are no longer needed. Interrupted and failed runs never reach this function.

    Backend-scoped memory stores invocation ids with its runtime prefix; resolve
    that prefix before deleting so the same run id in another backend namespace is
    left untouched.
    """

    run_id = str(run_id or "").strip()
    if not run_id:
        return 0
    target, stored_run_id = _storage_and_run_id(memory, run_id)
    lock = getattr(target, "_lock", None)
    connect = getattr(target, "_connect", None)
    close = getattr(target, "_close", None)
    if lock is None or not callable(connect) or not callable(close):
        return 0

    prefix = f"{stored_run_id}:"
    with lock:
        conn = connect()
        try:
            cursor = conn.execute(
                "delete from agent_skill_events where substr(invocation_id,1,?)=?",
                (len(prefix), prefix),
            )
            conn.commit()
            return max(0, int(cursor.rowcount or 0))
        except sqlite3.Error:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            return 0
        finally:
            close(conn)


__all__ = ["discard_completed_run_invocations"]
