from __future__ import annotations

import copy
import sqlite3
import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def _running_row(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "poll hot path",
        "status": "running",
        "events": [
            {
                "phase": "execute",
                "progress": 20,
                "payload": {"nested": {"values": [1, 2, 3]}},
            }
        ],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }


def test_active_poll_snapshot_remains_structurally_isolated():
    conversation = api_module.store.create_conversation("poll clone isolation", "search")
    run_id = "job-poll-clone-isolation"
    row = _running_row(run_id, conversation["id"])
    api_module.store.delete_run(run_id)
    assert api_module.store.reserve_run(
        run_id,
        conversation["id"],
        row["goal"],
        row,
        owner_id=api_module.WORKER_ID,
        lease_seconds=30,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = copy.deepcopy(row)

    visible = api_module.get_run(run_id)
    visible["events"][0]["payload"]["nested"]["values"].append(99)
    visible["events"][0]["progress"] = 88

    with api_module.RUN_LOCK:
        authoritative = api_module.RUNS[run_id]
        assert authoritative["events"][0]["progress"] == 20
        assert authoritative["events"][0]["payload"]["nested"]["values"] == [1, 2, 3]
        api_module.RUNS.pop(run_id, None)
    api_module.store.delete_run(run_id)


def test_run_status_reuses_point_read_until_database_changes(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "run-status-cache.db")
    conversation = store.create_conversation("status cache", "audit")
    run_id = "job-status-cache"
    row = _running_row(run_id, conversation["id"])
    assert store.reserve_run(
        run_id,
        conversation["id"],
        row["goal"],
        row,
        owner_id="worker-a",
        lease_seconds=30,
    )

    assert store.run_status(run_id) == "running"
    original_connect = store._connect
    calls = 0

    def counted_connect():
        nonlocal calls
        calls += 1
        return original_connect()

    monkeypatch.setattr(store, "_connect", counted_connect)

    assert store.run_status(run_id) == "running"
    assert calls == 0

    # An unrelated durable commit changes data_version, forcing one exact recheck.
    store.create_conversation("unrelated", "audit")
    assert store.run_status(run_id) == "running"
    assert calls >= 1


def test_run_status_cache_observes_other_store_terminal_commit(tmp_path):
    path = tmp_path / "run-status-cross-store.db"
    reader = WorkspaceStore(path)
    writer = WorkspaceStore(path)
    conversation = reader.create_conversation("cross store", "search")
    run_id = "job-status-cross-store"
    running = _running_row(run_id, conversation["id"])
    assert reader.reserve_run(
        run_id,
        conversation["id"],
        running["goal"],
        running,
        owner_id="worker-a",
        lease_seconds=30,
    )

    assert reader.run_status(run_id) == "running"
    completed = {
        **running,
        "status": "completed",
        "result": {"answer": "done"},
        "updated_at": time.time(),
    }
    assert writer.save_run(
        run_id,
        conversation["id"],
        running["goal"],
        "completed",
        completed,
        owner_id="worker-a",
    ) == "completed"

    assert reader.run_status(run_id) == "completed"


def test_run_status_cache_observes_external_sqlite_commit(tmp_path):
    path = tmp_path / "run-status-external.db"
    store = WorkspaceStore(path)
    conversation = store.create_conversation("external status", "search")
    run_id = "job-status-external"
    running = _running_row(run_id, conversation["id"])
    assert store.reserve_run(
        run_id,
        conversation["id"],
        running["goal"],
        running,
        owner_id="worker-a",
        lease_seconds=30,
    )
    assert store.run_status(run_id) == "running"

    with sqlite3.connect(path) as connection:
        connection.execute(
            "update runs set status='cancel_requested' where run_id=?",
            (run_id,),
        )
        connection.commit()

    assert store.run_status(run_id) == "cancel_requested"
