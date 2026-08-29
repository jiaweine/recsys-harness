from __future__ import annotations

import sqlite3
from typing import Any


RETIRED_SKILL_BUDGET = 24


def _storage(memory: Any) -> Any:
    """Resolve a backend-scoped facade to the SQLite owner."""

    return getattr(memory, "base_memory", memory)


def prune_retired_strategy_history(
    memory: Any,
    *,
    per_domain_limit: int = RETIRED_SKILL_BUDGET,
) -> int:
    """Bound unused retired strategies without weakening recovery idempotency.

    Trusted and active strategies remain untouched. Retired rows that are still
    referenced by ``agent_skill_events`` are also retained because an interrupted
    run may need the original skill row to replay an adaptive invocation without
    incrementing strategy state twice. Among the remaining retired history, keep
    the most recently updated rows independently for every catalog/domain pair.

    The maintenance path is best-effort: a storage error must never turn a
    successfully completed user run into a failure.
    """

    limit = max(0, int(per_domain_limit))
    target = _storage(memory)
    lock = getattr(target, "_lock", None)
    connect = getattr(target, "_connect", None)
    close = getattr(target, "_close", None)
    if lock is None or not callable(connect) or not callable(close):
        return 0

    with lock:
        conn = connect()
        try:
            cursor = conn.execute(
                """
                delete from agent_skills as doomed
                where doomed.status='retired'
                  and not exists (
                    select 1 from agent_skill_events as replay
                    where replay.catalog_key=doomed.catalog_key
                      and replay.domain=doomed.domain
                      and replay.fingerprint=doomed.fingerprint
                  )
                  and doomed.id not in (
                    select keep.id from agent_skills as keep
                    where keep.catalog_key=doomed.catalog_key
                      and keep.domain=doomed.domain
                      and keep.status='retired'
                    order by keep.updated_at desc, keep.score desc,
                             keep.wins desc, keep.id desc
                    limit ?
                  )
                """,
                (limit,),
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


__all__ = ["RETIRED_SKILL_BUDGET", "prune_retired_strategy_history"]
