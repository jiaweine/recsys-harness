from __future__ import annotations

import asyncio
import threading
import time

import lingjing_harness.api as api_module
from lingjing_harness.store import WorkspaceStore


def _run_snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "terminal takeover fencing regression",
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
    return store, conversation, run_id


def _take_over_and_complete(store: WorkspaceStore, run_id: str):
    takeover = WorkspaceStore(store.path)
    now = time.time()
    with takeover._lock, takeover._connect() as connection:  # noqa: SLF001 - deterministic expiry fixture
        connection.execute(
            "update runs set lease_until=? where run_id=?",
            (now - 1.0, run_id),
        )
        connection.commit()

    claimed = takeover.claim_recoverable_runs(
        owner_id="takeover-owner",
        lease_seconds=60.0,
        now=now,
    )
    assert [row["run_id"] for row in claimed] == [run_id]

    current = takeover.get_run(run_id)
    completed = {
        **current,
        "status": "completed",
        "result": {"answer": "successor result", "job_id": run_id},
        "updated_at": now + 1.0,
    }
    assert takeover.save_run(
        run_id,
        current["conversation_id"],
        current["goal"],
        "completed",
        completed,
        owner_id="takeover-owner",
        lease_seconds=60.0,
    ) == "completed"
    durable = takeover.get_run(run_id)
    assert durable["status"] == "completed"
    assert durable["result"]["answer"] == "successor result"
    return takeover


def _execute_in_thread(run_id: str, conversation_id: str, runner):
    errors: list[BaseException] = []

    def target() -> None:
        try:
            asyncio.run(
                api_module._execute(
                    run_id,
                    conversation_id,
                    "terminal takeover fencing regression",
                    runner,
                    catalog_revision=api_module.CATALOG_REVISION,
                )
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertion below
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, errors


def _assert_stale_worker_retired(run_id: str) -> None:
    for _ in range(100):
        with api_module.RUN_LOCK:
            present = run_id in api_module.RUNS
        if not present:
            break
        time.sleep(0.01)
    with api_module.RUN_LOCK:
        assert run_id not in api_module.RUNS
    assert api_module._PERSIST_META.get(run_id) is None


def test_terminal_takeover_fences_stale_worker_before_next_tool(monkeypatch, tmp_path):
    store, conversation, run_id = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        "terminal-before-tool",
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
                "title": "stale tool after successor completed",
                "detail": "must fail closed at the durable execute boundary",
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

    takeover = _take_over_and_complete(store, run_id)
    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    assert side_effects == []
    _assert_stale_worker_retired(run_id)
    durable = takeover.get_run(run_id)
    assert durable["status"] == "completed"
    assert durable["result"]["answer"] == "successor result"
    takeover.delete_run(run_id)


def test_terminal_get_does_not_disarm_stale_executor_fence(monkeypatch, tmp_path):
    store, conversation, run_id = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        "terminal-read-before-tool",
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
                "title": "stale tool after terminal read",
                "detail": "GET must not turn the local execution row terminal",
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

    takeover = _take_over_and_complete(store, run_id)
    visible = api_module.get_run(run_id)
    assert visible["status"] == "completed"
    assert visible["result"]["answer"] == "successor result"
    with api_module.RUN_LOCK:
        assert api_module.RUNS[run_id]["status"] == "running"

    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    assert side_effects == []
    _assert_stale_worker_retired(run_id)
    durable = takeover.get_run(run_id)
    assert durable["status"] == "completed"
    assert durable["result"]["answer"] == "successor result"
    takeover.delete_run(run_id)


def test_terminal_takeover_at_final_fence_converges_successor_payload(monkeypatch, tmp_path):
    store, conversation, run_id = _prepare_owned_run(
        monkeypatch,
        tmp_path,
        "terminal-at-final-fence",
    )
    started = threading.Event()
    release = threading.Event()

    def stale_run(self, text, **kwargs):
        started.set()
        assert release.wait(2.0)
        return {"answer": "stale runner result"}

    monkeypatch.setattr(api_module.AgentHarness, "run", stale_run)
    runner = api_module.harness.fork()
    thread, errors = _execute_in_thread(run_id, conversation["id"], runner)
    assert started.wait(2.0)

    takeover = _take_over_and_complete(store, run_id)
    release.set()
    thread.join(3.0)

    assert not thread.is_alive()
    assert errors == []
    with api_module.RUN_LOCK:
        local = dict(api_module.RUNS[run_id])
    assert local["status"] == "completed"
    assert local["result"]["answer"] == "successor result"
    assert "error" not in local
    assert api_module._PERSIST_META.get(run_id) is None

    visible = api_module.get_run(run_id)
    assert visible["status"] == "completed"
    assert visible["result"]["answer"] == "successor result"
    assert "error" not in visible
    takeover.delete_run(run_id)
