from __future__ import annotations

import sqlite3
import time
from typing import Any


def install_workspace_publication_fence(store_module: Any) -> None:
    """Keep a committed workspace revision fenced until its Catalog is active.

    Workspace imports span SQLite and the filesystem.  The normal workspace update
    lease protects the pre-commit phase, but a durable revision that has committed
    and not yet been published must survive even if that lease's wall-clock expiry
    passes.  Persist a publication revision in ``workspace_state`` and reject new
    writers/runs until a worker verifies that the active Catalog materializes that
    exact revision and clears the fence.

    The installer keeps the legacy ``commit_workspace_revision`` behaviour intact
    for callers that do not participate in file publication.  Workspace imports use
    the publication-specific commit/finalize methods added here.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_WORKSPACE_PUBLICATION_FENCE_INSTALLED", False):
        return

    original_init = cls._init
    original_commit = cls.commit_workspace_revision
    original_reserve_run = cls.reserve_run

    def _init(self) -> None:
        original_init(self)
        with self._lock, self._connect() as connection:
            columns = {
                row["name"]
                for row in connection.execute(
                    "pragma table_info(workspace_state)"
                ).fetchall()
            }
            if "publication_revision" not in columns:
                try:
                    connection.execute(
                        "alter table workspace_state add column publication_revision text"
                    )
                except sqlite3.OperationalError as migration_error:
                    # ``self._lock`` is process-local. Two workers first opening the
                    # same legacy DB can both observe the old schema before either
                    # ALTER commits. If a peer won that DDL race, accept the failed
                    # ALTER only after directly verifying the required column now
                    # exists. Any lock/corruption/unrelated failure is re-raised.
                    try:
                        connection.execute(
                            "select publication_revision from workspace_state limit 0"
                        )
                    except sqlite3.OperationalError:
                        raise migration_error
            connection.commit()

    def _workspace_update_row(self, connection, now: float):
        row = connection.execute(
            """
            select update_owner,update_until,updated_at,publication_revision
            from workspace_state where id=1
            """
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("update_owner") and float(data.get("updated_at") or 0.0) > now:
            repaired_until = self._reanchored_until(
                data.get("updated_at"), data.get("update_until"), now
            )
            connection.execute(
                "update workspace_state set update_until=?,updated_at=? where id=1",
                (repaired_until, now),
            )
            data["update_until"] = repaired_until
            data["updated_at"] = now
        return data

    def workspace_publication_pending(self, revision: str | None = None) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "select catalog_revision,publication_revision from workspace_state where id=1"
            ).fetchone()
        if not row:
            return False
        publication = str(row["publication_revision"] or "")
        if not publication:
            return False
        if revision is None:
            return True
        return publication == str(revision) and str(row["catalog_revision"] or "") == str(
            revision
        )

    def workspace_update_active(self, now: float | None = None) -> bool:
        now = time.time() if now is None else float(now)
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = self._workspace_update_row(connection, now)
            connection.commit()
        return bool(
            row
            and (
                row.get("publication_revision")
                or (
                    row.get("update_owner")
                    and float(row.get("update_until") or 0.0) > now
                )
            )
        )

    def begin_workspace_update(
        self,
        owner_id: str,
        *,
        lease_seconds: float = 120.0,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else float(now)
        until = now + max(5.0, float(lease_seconds))
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            active = connection.execute(
                "select 1 from runs where status in ('running','interrupted','cancel_requested') limit 1"
            ).fetchone()
            if active:
                connection.rollback()
                return False
            row = self._workspace_update_row(connection, now)
            if row and row.get("publication_revision"):
                connection.rollback()
                return False
            if (
                row
                and row.get("update_owner")
                and row.get("update_owner") != owner_id
                and float(row.get("update_until") or 0.0) > now
            ):
                connection.rollback()
                return False
            connection.execute(
                """
                update workspace_state
                set update_owner=?,update_until=?,publication_revision=null,updated_at=?
                where id=1
                """,
                (owner_id, until, now),
            )
            connection.commit()
        return True

    def commit_workspace_revision_for_publication(
        self, owner_id: str, revision: str
    ) -> bool:
        revision = str(revision or "").strip()
        if not revision:
            return False
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = self._workspace_update_row(connection, now)
            if (
                not row
                or row.get("publication_revision")
                or row.get("update_owner") != owner_id
                or float(row.get("update_until") or 0.0) <= now
            ):
                connection.rollback()
                return False
            active = connection.execute(
                "select 1 from runs where status in ('running','interrupted','cancel_requested') limit 1"
            ).fetchone()
            if active:
                connection.rollback()
                return False
            cursor = connection.execute(
                """
                update workspace_state
                set catalog_revision=?,publication_revision=?,updated_at=?
                where id=1 and update_owner=? and update_until>?
                  and (publication_revision is null or publication_revision='')
                """,
                (revision, revision, now, owner_id, now),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
        return True

    def finish_workspace_update(self, owner_id: str, revision: str) -> bool:
        revision = str(revision or "").strip()
        if not revision:
            return False
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            cursor = connection.execute(
                """
                update workspace_state
                set publication_revision=null,update_owner=null,update_until=null,updated_at=?
                where id=1 and update_owner=?
                  and catalog_revision=? and publication_revision=?
                """,
                (time.time(), owner_id, revision, revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
        return True

    def finish_workspace_publication(self, revision: str) -> bool:
        """Release a committed publication fence after verifying the active file.

        The caller must verify the active Catalog fingerprint before invoking this
        helper.  A new writer cannot acquire the workspace while
        ``publication_revision`` is present, so clearing by exact revision is safe
        for peer/restart recovery and cannot release a later writer's lease.
        """

        revision = str(revision or "").strip()
        if not revision:
            return False
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            cursor = connection.execute(
                """
                update workspace_state
                set publication_revision=null,update_owner=null,update_until=null,updated_at=?
                where id=1 and catalog_revision=? and publication_revision=?
                """,
                (time.time(), revision, revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
        return True

    def commit_workspace_revision(self, owner_id: str, revision: str) -> bool:
        committed = original_commit(self, owner_id, revision)
        if not committed:
            return False
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                update workspace_state set publication_revision=null
                where id=1 and catalog_revision=?
                """,
                (str(revision or "").strip(),),
            )
            connection.commit()
        return True

    def abort_workspace_update(self, owner_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("begin immediate")
            row = self._workspace_update_row(connection, time.time())
            if row and row.get("publication_revision"):
                connection.rollback()
                return
            connection.execute(
                """
                update workspace_state
                set update_owner=null,update_until=null,updated_at=?
                where id=1 and update_owner=?
                """,
                (time.time(), owner_id),
            )
            connection.commit()

    def reserve_run(self, *args, **kwargs):
        if self.workspace_publication_pending():
            return False
        return original_reserve_run(self, *args, **kwargs)

    cls._init = _init
    cls._workspace_update_row = _workspace_update_row
    cls.workspace_publication_pending = workspace_publication_pending
    cls.workspace_update_active = workspace_update_active
    cls.begin_workspace_update = begin_workspace_update
    cls.commit_workspace_revision_for_publication = (
        commit_workspace_revision_for_publication
    )
    cls.finish_workspace_update = finish_workspace_update
    cls.finish_workspace_publication = finish_workspace_publication
    cls.commit_workspace_revision = commit_workspace_revision
    cls.abort_workspace_update = abort_workspace_update
    cls.reserve_run = reserve_run
    cls._WORKSPACE_PUBLICATION_FENCE_INSTALLED = True


__all__ = ["install_workspace_publication_fence"]
