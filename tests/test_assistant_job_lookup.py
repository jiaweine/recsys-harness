from __future__ import annotations

import json
import time

from lingjing_harness.store import WorkspaceStore


def _reserve(store: WorkspaceStore, conversation_id: str, run_id: str, **extra):
    now = time.time()
    snapshot = {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "lookup",
        "status": "running",
        "events": [],
        "result": None,
        "created_at": now,
        "updated_at": now,
        **extra,
    }
    assert store.reserve_run(
        run_id,
        conversation_id,
        "lookup",
        snapshot,
        owner_id="lookup-worker",
        lease_seconds=30,
    )
    return snapshot


def test_fresh_run_assistant_lookup_never_scans_conversation_history(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "fresh-assistant-lookup.db")
    conversation = store.create_conversation("long history", "audit")

    for index in range(64):
        store.add_message(
            conversation["id"],
            "assistant",
            f"historical answer {index}",
            {"job_id": f"job-old-{index:03d}", "answer": "historical"},
        )

    _reserve(store, conversation["id"], "job-fresh")

    def fail_history_scan(_conversation_id: str):
        raise AssertionError("fresh run lookup must not load the full message history")

    monkeypatch.setattr(store, "list_messages", fail_history_scan)

    assert store.assistant_for_job(conversation["id"], "job-fresh") is None


def test_recovery_lookup_keeps_legacy_published_message_fallback(tmp_path, monkeypatch):
    store = WorkspaceStore(tmp_path / "recovery-assistant-lookup.db")
    conversation = store.create_conversation("legacy recovery", "audit")
    run_id = "job-legacy-published"
    _reserve(
        store,
        conversation["id"],
        run_id,
        events=[{"phase": "complete", "progress": 100}],
        checkpoint={"status": "completed", "result": {"answer": "legacy answer"}},
    )

    # Reproduce the durable state left by a pre-atomic publisher: the assistant
    # row exists while the run is still active.  Do not call add_message(), which
    # is intentionally wrapped by the current completion fence after api import.
    created_at = time.time()
    payload = {"job_id": run_id, "answer": "legacy answer"}
    expected = {
        "id": "msg-legacy-published",
        "conversation_id": conversation["id"],
        "role": "assistant",
        "content": "legacy answer",
        "payload": payload,
        "created_at": created_at,
    }
    with store._lock, store._connect() as connection:  # noqa: SLF001 - legacy DB fixture
        connection.execute(
            "update conversations set updated_at=? where id=?",
            (created_at, conversation["id"]),
        )
        connection.execute(
            """
            insert into messages(id,conversation_id,role,content,payload,created_at)
            values(?,?,?,?,?,?)
            """,
            (
                expected["id"],
                conversation["id"],
                "assistant",
                "legacy answer",
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )

    original_list_messages = store.list_messages
    scans = 0

    def track_history_scan(conversation_id: str):
        nonlocal scans
        scans += 1
        return original_list_messages(conversation_id)

    monkeypatch.setattr(store, "list_messages", track_history_scan)

    found = store.assistant_for_job(conversation["id"], run_id)

    assert found == expected
    assert scans == 1
