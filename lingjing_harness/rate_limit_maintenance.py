from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any


DEFAULT_RATE_LIMIT_GC_INTERVAL_SECONDS = 3600.0
DEFAULT_RATE_LIMIT_RETENTION_SECONDS = 86400.0


def install_rate_limit_maintenance(
    store: Any,
    *,
    interval_seconds: float = DEFAULT_RATE_LIMIT_GC_INTERVAL_SECONDS,
    retention_seconds: float = DEFAULT_RATE_LIMIT_RETENTION_SECONDS,
) -> None:
    """Add deterministic stale-key maintenance to one WorkspaceStore instance.

    The original rate-limit transaction remains authoritative for allowance and
    shared-worker counters.  Maintenance runs before the first use and then at a
    fixed wall-clock interval instead of depending on requests landing in a
    particular second.  Cleanup is deliberately best-effort: a transient SQLite
    maintenance failure must not create a new availability failure before the
    normal rate-limit transaction gets its own chance to proceed.
    """

    if getattr(store, "_rate_limit_maintenance_installed", False):
        return

    interval = max(60.0, float(interval_seconds))
    retention = max(interval, float(retention_seconds))
    maintenance_lock = threading.Lock()
    original_consume = store.consume_rate_limit
    next_gc_at = 0.0

    def cleanup(now: float) -> None:
        nonlocal next_gc_at
        # A wall-clock jump forward followed by rollback must not strand the
        # process-local maintenance deadline in the future for months or years.
        # If the stored deadline is more than one normal interval ahead of the
        # caller clock, re-arm cleanup immediately.  This changes only storage
        # hygiene; allowance/counter authority remains in WorkspaceStore.
        if next_gc_at > now + interval:
            next_gc_at = now
        if now < next_gc_at:
            return
        with maintenance_lock:
            if next_gc_at > now + interval:
                next_gc_at = now
            if now < next_gc_at:
                return
            try:
                with store._lock, store._connect() as connection:  # noqa: SLF001 - package-internal boundary
                    connection.execute("begin immediate")
                    connection.execute(
                        "create index if not exists idx_rate_limits_updated_at on rate_limits(updated_at)"
                    )
                    connection.execute(
                        "delete from rate_limits where updated_at<?",
                        (now - retention,),
                    )
                    connection.commit()
            except sqlite3.Error:
                # Retry sooner than the normal maintenance interval, but let the
                # authoritative consume transaction decide whether this request
                # can still proceed against the durable store.
                next_gc_at = now + min(interval, 60.0)
            else:
                next_gc_at = now + interval

    def maintained_consume_rate_limit(
        scope_key: str,
        *,
        limit: int,
        window_seconds: float,
        now: float | None = None,
    ) -> bool:
        effective_now = time.time() if now is None else float(now)
        cleanup(effective_now)
        return original_consume(
            scope_key,
            limit=limit,
            window_seconds=window_seconds,
            now=effective_now,
        )

    store.consume_rate_limit = maintained_consume_rate_limit
    store._rate_limit_maintenance_installed = True
    store._rate_limit_maintenance_cleanup = cleanup
