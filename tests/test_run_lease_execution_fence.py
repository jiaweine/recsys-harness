from __future__ import annotations

import asyncio
import threading
import time

import pytest

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def _run_snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "lease fencing regression",
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


def _prepare_owned_run(monkeypatch, tmp_path, name: str):
    store = WorkspaceStore(tmp_path / f"{name}.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation(name, "search")
    run_id = f"job-{name}"
    row = _run_snapshot(run_id, conversation["id"])
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
    return store, conversation, run_id, row


def _take_over(store: WorkspaceStore, run_id: str, owner_id: str = "takeover-owner"):
    takeover = WorkspaceStore(store.path)
    now = time.time()
    with takeover._lock, takeover._connect() as connection:  # noqa: SLF001 - deterministic expiry fixture
        connection.execute(
            "update runs set lease_until=? where run_id=?",
            (now - 1.0, run_id),
        )
        connection.commit()
    claimed = takeover.claim_recoverable_runs(
        owner_id=owner_id,
        lease_seconds=60.0,
        now=now,
    )
    assert run_id in {str(row["run_id"]) for row in claimed}
    current = takeover.get_run(run_id)
    assert current["owner_id"] == owner_id
    assert current["status"] == "running"
    return takeover


def _execute_in_thread(run_id: str, conversation_id: str, runner):
    errors: list[BaseException] = []

    def target() -> None:
        try:
            asyncio.run(
                api_module._execute(
                    run_id,
                    conversation_id,
                    "lease fencing regression",
                    runner,
                    catalog_revision=api_module.CATALOG_REVISION,
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, errors


def _assert_stale_worker_retired(store: WorkspaceStore, run_id: str) -> None:
    for _ in range(100):
        with api_module.RUN_LOCK:
            present = run_id in api_module.RUNS
        if not present:
            break
        time.sleep(0.01)
    with api_module.RUN_LOCK:
        assert run_id not in api_module.RUNS
    assert api_module._PERSIST_META.get(run_id) is None
    assert store.assistant_for_job(store.get_run(run_id)["conversation_id"], run_id) is None


def test_takeover_fences_stale_worker_before_execute_side_effect(monkeypatch, tmp_path):
    store, conversation, run_id, _row = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        "lease-before-tool",
    )
    started = threading.Event()
    release = threading.Event()
    side_effects: list[str] = []

    def stale_run(self, text, *, sink=None, **kwargs):
        started.set()
        assert release.wait(2.0)
        assert sink is not None
        sink(
            {
                "phase": "execute",
                "title": "stale tool",
                "detail": "must be fenced before execution",
                "progress": 20,
                "payload": {},
                "created_at": time.time(),
            }
        )
        side_effects.append("tool-ran")
        return {"answer": "stale"}

    monkeypatch.setattr(api_module.AgentHarness, "run", stale_run)
    runner = api_module.harness.fork()
    thread, errors = _execute_in_thread(run_id, conversation["id"], runner)
    assert started.wait(2.0)
    takeover = _take_over(store, run_id)
    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    assert side_effects == []
    _assert_stale_worker_retired(takeover, run_id)
    assert takeover.get_run(run_id)["owner_id"] == "takeover-owner"
    takeover.delete_run(run_id, owner_id="takeover-owner")


@pytest.mark.parametrize("method_name", ["update_policy", "record_episode"])
def test_takeover_fences_non_idempotent_final_learning(
    monkeypatch,
    tmp_path,
    method_name: str,
):
    store, conversation, run_id, _row = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        f"lease-learning-{method_name}",
    )
    started = threading.Event()
    release = threading.Event()
    writes: list[str] = []

    def underlying_write(*args, **kwargs):
        writes.append(method_name)

    monkeypatch.setattr(api_module.memory, method_name, underlying_write)

    def stale_run(self, text, **kwargs):
        started.set()
        assert release.wait(2.0)
        if method_name == "update_policy":
            self.memory.update_policy("search", ["search|stale"], 0.7)
        else:
            self.memory.record_episode(
                self.catalog_key,
                "stale goal",
                "search",
                0.7,
                findings=["stale"],
                action_keys=["search|stale"],
                learned=[],
            )
        return {"answer": "stale"}

    monkeypatch.setattr(api_module.AgentHarness, "run", stale_run)
    runner = api_module.harness.fork()
    thread, errors = _execute_in_thread(run_id, conversation["id"], runner)
    assert started.wait(2.0)
    takeover = _take_over(store, run_id)
    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    assert writes == []
    _assert_stale_worker_retired(takeover, run_id)
    takeover.delete_run(run_id, owner_id="takeover-owner")


def test_takeover_fences_stale_assistant_publish_after_runner_returns(monkeypatch, tmp_path):
    store, conversation, run_id, _row = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        "lease-before-message",
    )
    started = threading.Event()
    release = threading.Event()

    def stale_run(self, text, **kwargs):
        started.set()
        assert release.wait(2.0)
        return {"answer": "stale assistant answer"}

    monkeypatch.setattr(api_module.AgentHarness, "run", stale_run)
    runner = api_module.harness.fork()
    thread, errors = _execute_in_thread(run_id, conversation["id"], runner)
    assert started.wait(2.0)
    takeover = _take_over(store, run_id)
    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    _assert_stale_worker_retired(takeover, run_id)
    assert takeover.assistant_for_job(conversation["id"], run_id) is None
    assert takeover.get_run(run_id)["status"] == "running"
    assert takeover.get_run(run_id)["owner_id"] == "takeover-owner"
    takeover.delete_run(run_id, owner_id="takeover-owner")
