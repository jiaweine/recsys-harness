import threading
import time

import pytest

from lingjing_harness.api_shutdown import WorkerShutdown, guard_runner_for_shutdown
from lingjing_harness.runtime import AgentHarness, AgentMemory
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore
from lingjing_harness.store_handoff import release_interrupted_run


def test_shutdown_signal_is_observed_after_checkpoint(tmp_path):
    memory = AgentMemory(tmp_path / "memory.db")
    runner = AgentHarness(build_sample_catalog(), memory=memory)
    shutdown = threading.Event()
    checkpoints = []

    original_run = guard_runner_for_shutdown(runner, shutdown)

    def checkpoint(payload):
        checkpoints.append(payload)
        shutdown.set()

    try:
        with pytest.raises(WorkerShutdown):
            runner.run(
                "检查‘露营灯’的搜索体验并自主优化",
                checkpoint_sink=checkpoint,
            )
    finally:
        runner.run = original_run

    assert len(checkpoints) == 1
    assert len(checkpoints[0]["actions"]) == 1
    assert checkpoints[0]["actions"][0]["tool"] == "data.inspect"


def test_explicit_user_cancel_wins_over_worker_shutdown():
    runner = AgentHarness(build_sample_catalog())
    shutdown = threading.Event()
    shutdown.set()
    original_run = guard_runner_for_shutdown(runner, shutdown)

    try:
        from lingjing_harness.runtime import RunCancelled

        with pytest.raises(RunCancelled):
            runner.run(
                "做一次全局体检",
                should_stop=lambda: True,
            )
    finally:
        runner.run = original_run


def test_interrupted_handoff_is_immediately_claimable(tmp_path):
    path = tmp_path / "handoff.db"
    old_worker = WorkspaceStore(path)
    new_worker = WorkspaceStore(path)
    conversation = old_worker.create_conversation()
    now = time.time()
    snapshot = {
        "run_id": "run-handoff",
        "conversation_id": conversation["id"],
        "goal": "resume me",
        "status": "running",
        "events": [{"phase": "execute", "progress": 30}],
        "checkpoint": {"cycle": 1, "status": "running", "actions": [{"tool": "data.inspect"}]},
        "created_at": now,
        "updated_at": now,
    }
    assert old_worker.reserve_run(
        "run-handoff",
        conversation["id"],
        "resume me",
        snapshot,
        owner_id="old-worker",
        lease_seconds=30,
    )

    interrupted = {**snapshot, "status": "interrupted", "updated_at": now + 0.1}
    assert release_interrupted_run(
        old_worker,
        "run-handoff",
        "old-worker",
        interrupted,
        now=now + 0.1,
    )

    durable = old_worker.get_run("run-handoff")
    assert durable["status"] == "interrupted"
    assert durable["owner_id"] is None
    assert durable["lease_until"] is None

    claimed = new_worker.claim_recoverable_runs(
        owner_id="new-worker",
        lease_seconds=30,
        now=now + 0.1,
    )
    assert [row["run_id"] for row in claimed] == ["run-handoff"]
    assert new_worker.get_run("run-handoff")["owner_id"] == "new-worker"


def test_cancel_request_cannot_be_rewritten_as_interrupted(tmp_path):
    path = tmp_path / "cancel-handoff.db"
    store = WorkspaceStore(path)
    conversation = store.create_conversation()
    now = time.time()
    snapshot = {
        "run_id": "run-cancel-handoff",
        "conversation_id": conversation["id"],
        "goal": "cancel me",
        "status": "running",
        "events": [],
        "created_at": now,
        "updated_at": now,
    }
    assert store.reserve_run(
        "run-cancel-handoff",
        conversation["id"],
        "cancel me",
        snapshot,
        owner_id="old-worker",
        lease_seconds=30,
    )
    assert store.request_cancel("run-cancel-handoff") == "cancel_requested"

    assert not release_interrupted_run(
        store,
        "run-cancel-handoff",
        "old-worker",
        {**snapshot, "status": "interrupted"},
        now=now + 0.2,
    )
    assert store.run_status("run-cancel-handoff") == "cancel_requested"
