from __future__ import annotations

import asyncio
import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


class _Memory:
    def record_episode(self, *args, **kwargs):
        return None

    def update_policy(self, *args, **kwargs):
        return None


class _Runner:
    def __init__(self) -> None:
        self.memory = _Memory()

    def run(self, text, **kwargs):
        return {"answer": "stale worker answer", "events": []}


def _snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "assistant publication terminal takeover",
        "status": "running",
        "events": [],
        "result": None,
        "attachment_ids": [],
        "attachments": [],
        "allow_network": False,
        "catalog_revision": api_module.CATALOG_REVISION,
        "created_at": now,
        "updated_at": now,
    }


def test_terminal_successor_at_assistant_publish_retires_stale_local_run(
    monkeypatch, tmp_path
):
    store = WorkspaceStore(tmp_path / "assistant-publish-terminal-takeover.db")
    successor = WorkspaceStore(store.path)
    monkeypatch.setattr(api_module, "store", store)

    conversation = store.create_conversation("terminal takeover", "search")
    run_id = "job-assistant-publish-terminal-takeover"
    row = _snapshot(run_id, conversation["id"])
    assert store.reserve_run(
        run_id,
        conversation["id"],
        row["goal"],
        row,
        owner_id=api_module.WORKER_ID,
        lease_seconds=api_module.RUN_LEASE_SECONDS,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = row
    api_module._PERSIST_META.pop(run_id, None)

    original_publish = store.publish_run_completion
    successor_owner = "worker-b:run:successor"
    successor_messages: list[str] = []

    def successor_wins_before_stale_publication(*args, **kwargs):
        # Model worker A being paused after runner completion until its lease is
        # recoverable.  Worker B claims and completes the run before A enters the
        # atomic assistant-publication transaction.
        with store._lock, store._connect() as connection:  # noqa: SLF001 - crash/takeover fixture
            connection.execute(
                "update runs set lease_until=0 where run_id=?",
                (run_id,),
            )
            connection.commit()

        claimed = successor.claim_recoverable_runs(
            owner_id=successor_owner,
            lease_seconds=30.0,
            limit=1,
        )
        assert [saved["run_id"] for saved in claimed] == [run_id]

        successor_payload = {
            "job_id": run_id,
            "answer": "successor answer",
            "events": [],
            "catalog_revision": api_module.CATALOG_REVISION,
        }
        status, message = successor.publish_run_completion(
            conversation["id"],
            "successor answer",
            successor_payload,
            owner_id=successor_owner,
        )
        assert status == "completed"
        assert message is not None
        successor_messages.append(message["id"])

        # Worker A now observes the terminal successor.  Its publication must be
        # rejected without leaving a terminal-but-stale local cache row behind.
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(
        store,
        "publish_run_completion",
        successor_wins_before_stale_publication,
    )

    asyncio.run(
        api_module._execute(
            run_id,
            conversation["id"],
            row["goal"],
            _Runner(),
            catalog_revision=api_module.CATALOG_REVISION,
        )
    )

    assert len(successor_messages) == 1
    with api_module.RUN_LOCK:
        assert run_id not in api_module.RUNS
    assert run_id not in api_module._PERSIST_META

    durable = store.get_run(run_id)
    assert durable["status"] == "completed"
    assert durable["result"]["answer"] == "successor answer"
    assert "error" not in durable

    observed = api_module.get_run(run_id)
    assert observed["status"] == "completed"
    assert observed["result"]["answer"] == "successor answer"
    assert "error" not in observed

    messages = [
        message
        for message in store.list_messages(conversation["id"])
        if message["role"] == "assistant"
    ]
    assert [message["id"] for message in messages] == successor_messages
    assert messages[0]["payload"]["answer"] == "successor answer"
