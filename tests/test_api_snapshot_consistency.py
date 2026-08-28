import time
import uuid

import pytest

import lingjing_harness.api as api_module


class _MutatingCheckpoint(dict):
    def __deepcopy__(self, memo):
        raise RuntimeError("dictionary changed size during iteration")


class _UnexpectedCopyFailure(dict):
    def __deepcopy__(self, memo):
        raise RuntimeError("unexpected deepcopy failure")


def _install_active_run(checkpoint):
    run_id = f"job-snapshot-{uuid.uuid4().hex[:10]}"
    conversation = api_module.store.create_conversation("snapshot race", "search")
    now = time.time()
    durable = {
        "run_id": run_id,
        "conversation_id": conversation["id"],
        "goal": "snapshot race",
        "status": "running",
        "events": [],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    assert api_module.store.reserve_run(
        run_id,
        conversation["id"],
        durable["goal"],
        durable,
        owner_id=api_module.WORKER_ID,
        lease_seconds=api_module.RUN_LEASE_SECONDS,
    )
    in_memory = dict(durable)
    in_memory["checkpoint"] = checkpoint
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = in_memory
    return run_id, durable


def _remove_active_run(run_id):
    with api_module.RUN_LOCK:
        api_module.RUNS.pop(run_id, None)
    api_module.store.delete_run(run_id, owner_id=api_module.WORKER_ID)


def test_get_run_falls_back_to_durable_snapshot_when_nested_copy_races():
    run_id, durable = _install_active_run(_MutatingCheckpoint({"cycle": 1}))
    try:
        snapshot = api_module.get_run(run_id)
        assert snapshot["run_id"] == run_id
        assert snapshot["status"] == "running"
        assert snapshot["events"] == durable["events"]
        assert "checkpoint" not in snapshot
    finally:
        _remove_active_run(run_id)


def test_get_run_does_not_hide_unrelated_deepcopy_errors():
    run_id, _ = _install_active_run(_UnexpectedCopyFailure({"cycle": 1}))
    try:
        with pytest.raises(RuntimeError, match="unexpected deepcopy failure"):
            api_module.get_run(run_id)
    finally:
        _remove_active_run(run_id)
