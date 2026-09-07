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

    def run(self, text, *, should_stop=None, **kwargs):
        assert should_stop is not None
        assert should_stop() is False
        return {"answer": "must not be published", "events": []}


def _snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "assistant publish cancel race",
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


def test_remote_cancel_after_runner_return_still_wins_before_assistant_publish(
    monkeypatch, tmp_path
):
    store = WorkspaceStore(tmp_path / "assistant-publish-cancel.db")
    remote = WorkspaceStore(store.path)
    monkeypatch.setattr(api_module, "store", store)

    conversation = store.create_conversation("publish cancel race", "search")
    run_id = "job-publish-cancel-race"
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
    publication_attempts: list[str] = []

    def cancel_at_publication(*args, **kwargs):
        # runner.run() and the existing completion-time cancel check have already
        # returned.  Inject the remote stop at the final durable publication
        # boundary: old behavior inserted the assistant and later overwrote this
        # successful cancel with completed.
        publication_attempts.append("publish")
        assert remote.run_status(run_id) == "running"
        assert remote.request_cancel(run_id) == "cancel_requested"
        return original_publish(*args, **kwargs)

    monkeypatch.setattr(store, "publish_run_completion", cancel_at_publication)

    asyncio.run(
        api_module._execute(
            run_id,
            conversation["id"],
            row["goal"],
            _Runner(),
            catalog_revision=api_module.CATALOG_REVISION,
        )
    )

    assert publication_attempts == ["publish"]
    saved = store.get_run(run_id)
    assert saved["status"] == "cancelled"
    assert saved["events"][-1]["phase"] == "cancel"
    assert store.assistant_for_job(conversation["id"], run_id) is None
    with api_module.RUN_LOCK:
        local = api_module.RUNS.get(run_id)
        assert local is not None and local["status"] == "cancelled"
        api_module.RUNS.pop(run_id, None)
    api_module._PERSIST_META.pop(run_id, None)
    store.delete_run(run_id)
