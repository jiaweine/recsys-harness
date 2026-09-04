from __future__ import annotations

import sqlite3
from typing import Any


def install_workspace_run_schema_migration_guard(store_module: Any) -> None:
    """Make legacy ``runs`` lease-column upgrades safe across workers.

    ``WorkspaceStore._init`` historically upgrades old ``runs`` tables by reading
    ``pragma table_info(runs)`` and then conditionally issuing ``alter table``.
    Its lock is process-local, so two workers can both observe the old schema and
    race the same DDL.  Preflight an existing legacy table before the original
    initializer runs and accept a raced ALTER only when the required column is
    directly queryable afterward.
    """

    cls = store_module.WorkspaceStore
    if getattr(cls, "_WORKSPACE_RUN_SCHEMA_MIGRATION_GUARD_INSTALLED", False):
        return

    original_init = cls._init

    def _ensure_column(connection, *, column: str, ddl: str) -> None:
        columns = {
            row["name"]
            for row in connection.execute("pragma table_info(runs)").fetchall()
        }
        if column in columns:
            return
        try:
            connection.execute(ddl)
        except sqlite3.OperationalError as migration_error:
            # Another process may have won the DDL race after our schema read.
            # Accept that failure only when the exact required postcondition is
            # now true; lock, corruption, or unrelated DDL failures propagate.
            try:
                connection.execute(f"select {column} from runs limit 0")
            except sqlite3.OperationalError:
                raise migration_error

    def _init(self) -> None:
        with self._lock, self._connect() as connection:
            runs_exists = connection.execute(
                "select 1 from sqlite_master where type='table' and name='runs'"
            ).fetchone()
            if runs_exists:
                _ensure_column(
                    connection,
                    column="owner_id",
                    ddl="alter table runs add column owner_id text",
                )
                _ensure_column(
                    connection,
                    column="lease_until",
                    ddl="alter table runs add column lease_until real",
                )
                connection.commit()
        original_init(self)

    cls._init = _init
    cls._WORKSPACE_RUN_SCHEMA_MIGRATION_GUARD_INSTALLED = True
