from __future__ import annotations

import asyncio
import threading
import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def _snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "cancel side-effect fence regression",
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


def _prepare(monkeypatch, tmp_path, name: str):
    store = WorkspaceStore(tmp_path / f"{name}.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation(name, "search")
    run_id = f"job-{name}"
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
    return store, WorkspaceStore(store.path), conversation, run_id


def _run_scenario(run_id: str, conversation_id: str, runner, ready, release, remote):
    async def scenario() -> None:
        task = asyncio.create_task(
            api_module._execute(
                run_id,
                conversation_id,
                "cancel side-effect fence regression",
                runner,
                catalog_revision=api_module.CATALOG_REVISION,
            )
        )
        assert await asyncio.to_thread(ready.wait, 2.0)
        assert remote.request_cancel(run_id) == "cancel_requested"
        # The request intentionally lands after the runner's cooperative stop
        # poll, so only the durable side-effect fence can prevent the next action.
        with api_module.RUN_LOCK:
            assert api_module.RUNS[run_id]["status"] == "running"
        release.set()
        await asyncio.wait_for(task, timeout=3.0)

    asyncio.run(scenario())


def _assert_cancelled(store: WorkspaceStore, conversation_id: str, run_id: str) -> None:
    saved = store.get_run(run_id)
    assert saved["status"] == "cancelled"
    assert saved["events"][-1]["phase"] == "cancel"
    assert store.assistant_for_job(conversation_id, run_id) is None
    with api_module.RUN_LOCK:
        local = api_module.RUNS.get(run_id)
        assert local is not None and local["status"] == "cancelled"
        api_module.RUNS.pop(run_id, None)
    api_module._PERSIST_META.pop(run_id, None)
    store.delete_run(run_id)


class _Memory:
    def __init__(self, side_effects: list[str]) -> None:
        self.side_effects = side_effects

    def record_episode(self, *args, **kwargs):
        self.side_effects.append("learning-ran")

    def update_policy(self, *args, **kwargs):
        self.side_effects.append("policy-ran")


class _Runner:
    def __init__(self, run_impl, side_effects: list[str] | None = None) -> None:
        self.run = run_impl.__get__(self, type(self))
        self.memory = _Memory(side_effects if side_effects is not None else [])


def test_remote_cancel_after_stop_poll_blocks_next_tool(monkeypatch, tmp_path):
    store, remote, conversation, run_id = _prepare(
        monkeypatch, tmp_path, "cancel-before-tool"
    )
    ready = threading.Event()
    release = threading.Event()
    side_effects: list[str] = []

    def run_impl(self, text, *, sink=None, should_stop=None, **kwargs):
        assert should_stop is not None and should_stop() is False
        ready.set()
        assert release.wait(2.0)
        assert sink is not None
        sink(
            {
                "phase": "execute",
                "title": "tool linearization boundary",
                "detail": "remote cancel must win before this tool starts",
                "progress": 20,
                "payload": {},
                "created_at": time.time(),
            }
        )
        side_effects.append("tool-ran")
        return {"answer": "unexpected"}

    runner = _Runner(run_impl)
    _run_scenario(run_id, conversation["id"], runner, ready, release, remote)

    assert side_effects == []
    _assert_cancelled(store, conversation["id"], run_id)


def test_remote_cancel_after_stop_poll_blocks_learning(monkeypatch, tmp_path):
    store, remote, conversation, run_id = _prepare(
        monkeypatch, tmp_path, "cancel-before-learning"
    )
    ready = threading.Event()
    release = threading.Event()
    side_effects: list[str] = []

    def run_impl(self, text, *, should_stop=None, **kwargs):
        assert should_stop is not None and should_stop() is False
        ready.set()
        assert release.wait(2.0)
        self.memory.record_episode("episode")
        side_effects.append("after-learning")
        return {"answer": "unexpected"}

    runner = _Runner(run_impl, side_effects)
    _run_scenario(run_id, conversation["id"], runner, ready, release, remote)

    assert side_effects == []
    _assert_cancelled(store, conversation["id"], run_id)


def test_remote_cancel_before_runner_returns_blocks_assistant_publish(monkeypatch, tmp_path):
    store, remote, conversation, run_id = _prepare(
        monkeypatch, tmp_path, "cancel-before-publish"
    )
    ready = threading.Event()
    release = threading.Event()
    returned: list[str] = []

    def run_impl(self, text, *, should_stop=None, **kwargs):
        assert should_stop is not None and should_stop() is False
        ready.set()
        assert release.wait(2.0)
        returned.append("runner-finished")
        return {"answer": "must not be published", "events": []}

    runner = _Runner(run_impl)
    _run_scenario(run_id, conversation["id"], runner, ready, release, remote)

    assert returned == ["runner-finished"]
    _assert_cancelled(store, conversation["id"], run_id)
