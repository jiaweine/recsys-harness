from __future__ import annotations

import sqlite3
import threading
from typing import Any

from lingjing_harness.store import WorkspaceStore


class _BarrierCursor:
    def __init__(self, cursor: sqlite3.Cursor, barrier: threading.Barrier) -> None:
        self._cursor = cursor
        self._barrier = barrier

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._barrier.wait(timeout=10)
        return rows

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _BarrierConnection:
    def __init__(self, connection: sqlite3.Connection, barrier: threading.Barrier) -> None:
        self._connection = connection
        self._barrier = barrier

    def execute(self, sql: str, *args, **kwargs):
        cursor = self._connection.execute(sql, *args, **kwargs)
        if sql.strip().lower() == "pragma table_info(runs)":
            return _BarrierCursor(cursor, self._barrier)
        return cursor

    def __enter__(self):
        self._connection.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._connection.__exit__(exc_type, exc, tb)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def _create_legacy_store_db(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            create table runs(
              run_id text primary key,
              conversation_id text not null,
              goal text not null,
              status text not null,
              snapshot text not null,
              created_at real not null,
              updated_at real not null
            )
            """
        )
        connection.execute(
            """
            create table workspace_state(
              id integer primary key check(id=1),
              catalog_revision text not null default '',
              update_owner text,
              update_until real,
              updated_at real not null,
              publication_revision text
            )
            """
        )
        connection.execute(
            "insert into workspace_state(id,catalog_revision,updated_at) values(1,'rev-a',1.0)"
        )


def test_concurrent_first_upgrade_installs_run_lease_columns_once(tmp_path, monkeypatch):
    path = tmp_path / "store-schema-race.db"
    _create_legacy_store_db(path)

    original_connect = WorkspaceStore._connect
    barrier = threading.Barrier(2)

    def synchronized_connect(self):
        return _BarrierConnection(original_connect(self), barrier)

    monkeypatch.setattr(WorkspaceStore, "_connect", synchronized_connect)

    errors: list[BaseException] = []

    def boot_worker() -> None:
        try:
            WorkspaceStore(path)
        except BaseException as exc:  # noqa: BLE001 - capture the startup failure contract
            errors.append(exc)

    workers = [threading.Thread(target=boot_worker) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []

    with sqlite3.connect(path) as connection:
        columns = [row[1] for row in connection.execute("pragma table_info(runs)")]
    assert columns.count("owner_id") == 1
    assert columns.count("lease_until") == 1
