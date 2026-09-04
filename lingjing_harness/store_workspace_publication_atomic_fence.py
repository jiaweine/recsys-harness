from __future__ import annotations

from typing import Any


_PUBLICATION_OWNER_SENTINEL = "__workspace_publication_fence__"


def install_workspace_publication_atomic_fence(store_module: Any) -> None:
    """Expose durable publication as an unexpired lease inside store transactions.

    The publication layer persists ``publication_revision`` beyond the ordinary
    update lease. Some legacy/core store methods, most importantly ``reserve_run``,
    make their correctness decision from ``_workspace_update_row`` inside the same
    ``BEGIN IMMEDIATE`` transaction that performs the write. Make a pending
    publication look like a permanently active reserved owner in that descriptor so
    those transaction-local checks cannot be bypassed by a stale wrapper precheck or
    by expiry of the original timed lease.

    Only the returned descriptor is changed. Durable ``update_owner`` and
    ``update_until`` remain untouched, so exact-owner publication finalization and
    peer/restart recovery continue to operate on the original persisted state.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_WORKSPACE_PUBLICATION_ATOMIC_FENCE_INSTALLED", False):
        return

    original_workspace_update_row = cls._workspace_update_row

    def _workspace_update_row(self, connection, now: float):
        row = original_workspace_update_row(self, connection, now)
        if not row or not row.get("publication_revision"):
            return row
        fenced = dict(row)
        fenced["update_owner"] = _PUBLICATION_OWNER_SENTINEL
        fenced["update_until"] = float("inf")
        return fenced

    cls._workspace_update_row = _workspace_update_row
    cls._WORKSPACE_PUBLICATION_ATOMIC_FENCE_INSTALLED = True


__all__ = ["install_workspace_publication_atomic_fence"]
