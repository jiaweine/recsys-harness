from __future__ import annotations

import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def test_conversation_list_snapshot_marks_active_rows(tmp_path):
    store = WorkspaceStore(tmp_path / "conversation-list-snapshot.db")
    one = store.create_conversation("one", "audit")
    two = store.create_conversation("two", "search")
    three = store.create_conversation("three", "recommend")

    now = time.time()
    active = {
        "run_id": "job-list-active",
        "conversation_id": two["id"],
        "goal": "active",
        "status": "running",
        "events": [],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    assert store.reserve_run(
        active["run_id"],
        two["id"],
        active["goal"],
        active,
        owner_id="worker-a",
        lease_seconds=30,
    )

    rows = store.list_conversations_with_active(limit=40)
    by_id = {row["id"]: row for row in rows}

    assert by_id[one["id"]]["active"] is False
    assert by_id[two["id"]]["active"] is True
    assert by_id[three["id"]]["active"] is False


def test_conversation_list_route_uses_single_snapshot_helper(monkeypatch, tmp_path):
    store = WorkspaceStore(tmp_path / "conversation-list-route.db")
    conversation = store.create_conversation("route", "audit")
    monkeypatch.setattr(api_module, "store", store)

    def unexpected(*args, **kwargs):
        raise AssertionError("route must not split list and active reads")

    monkeypatch.setattr(store, "list_conversations", unexpected)
    monkeypatch.setattr(store, "active_conversation_ids", unexpected)

    rows = api_module.conversations()

    assert len(rows) == 1
    assert rows[0]["id"] == conversation["id"]
    assert rows[0]["active"] is False
