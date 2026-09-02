from __future__ import annotations

import asyncio
import copy
import sqlite3
import threading
import time

import pytest

import lingjing_harness.api as api_module
import lingjing_harness.runtime as runtime_module
from lingjing_harness.runtime import AgentHarness, AgentMemory
from lingjing_harness.runtime.run_finalization import commit_run_learning
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.store import WorkspaceStore


def _final_learning_counts(path) -> dict[str, int]:
    with sqlite3.connect(path) as conn:
        return {
            "markers": int(conn.execute("select count(*) from agent_run_learning_events").fetchone()[0]),
            "episodes": int(conn.execute("select count(*) from agent_episodes").fetchone()[0]),
            "policy_trials": int(
                conn.execute("select coalesce(sum(trials),0) from agent_policy_stats").fetchone()[0]
            ),
        }


def _commit(memory: AgentMemory, event_key: str) -> dict:
    return commit_run_learning(
        memory,
        event_key,
        context_key="search",
        action_keys=["search|search.run", "search|search.run", "search|search.audit"],
        policy_reward=0.75,
        catalog_key="catalog",
        goal="crash-consistency",
        mode="search",
        episode_reward=0.75,
        findings=["one"],
        learned=[],
    )


def test_atomic_final_learning_rolls_back_policy_marker_and_episode_together(tmp_path):
    path = tmp_path / "atomic-rollback.db"
    memory = AgentMemory(path)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            create trigger fail_final_episode
            before insert on agent_episodes
            begin
              select raise(abort, 'forced final-learning crash');
            end
            """
        )
        conn.commit()

    with pytest.raises(sqlite3.DatabaseError, match="forced final-learning crash"):
        _commit(memory, "api:rollback")

    assert _final_learning_counts(path) == {
        "markers": 0,
        "episodes": 0,
        "policy_trials": 0,
    }

    with sqlite3.connect(path) as conn:
        conn.execute("drop trigger fail_final_episode")
        conn.commit()

    applied = _commit(memory, "api:rollback")
    assert applied["applied"] is True
    assert applied["atomic"] is True
    assert _final_learning_counts(path) == {
        "markers": 1,
        "episodes": 1,
        "policy_trials": 2,
    }


def test_same_final_learning_event_is_deduplicated_across_connections(tmp_path):
    path = tmp_path / "cross-connection.db"
    memory_a = AgentMemory(path)
    memory_b = AgentMemory(path)
    barrier = threading.Barrier(2)
    results: list[dict] = []
    errors: list[BaseException] = []

    def worker(memory: AgentMemory) -> None:
        try:
            barrier.wait(timeout=2.0)
            results.append(_commit(memory, "api:shared-job"))
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(memory_a,), daemon=True),
        threading.Thread(target=worker, args=(memory_b,), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)

    assert errors == []
    assert all(not thread.is_alive() for thread in threads)
    assert sorted(bool(row["applied"]) for row in results) == [False, True]
    assert sum(bool(row["deduplicated"]) for row in results) == 1
    assert _final_learning_counts(path) == {
        "markers": 1,
        "episodes": 1,
        "policy_trials": 2,
    }


def test_completed_checkpoint_waits_for_public_finalizers_and_retry_deduplicates_learning(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "checkpoint-commit-marker.db"
    memory = AgentMemory(path)
    catalog = build_sample_catalog()
    checkpoints: list[dict] = []
    original_credit = runtime_module.apply_semantic_trajectory_credit

    def checkpoint_sink(payload: dict) -> None:
        checkpoints.append(copy.deepcopy(payload))

    def crash_after_base_learning(*args, **kwargs):
        raise RuntimeError("simulated crash before public finalization")

    first = AgentHarness(catalog, memory=memory)
    first._finalization_event_key = "api:checkpoint-job"  # noqa: SLF001 - production API contract
    monkeypatch.setattr(runtime_module, "apply_semantic_trajectory_credit", crash_after_base_learning)
    with pytest.raises(RuntimeError, match="simulated crash before public finalization"):
        first.run(
            "做一次全局体检，告诉我最值得先处理的问题",
            checkpoint_sink=checkpoint_sink,
        )

    # Base verifier and atomic final learning have finished, but the correctness-
    # bearing public finalizers have not. A completed recovery marker must not exist.
    assert checkpoints
    assert all(row.get("status") != "completed" for row in checkpoints)
    after_crash = _final_learning_counts(path)
    assert after_crash["markers"] == 1
    assert after_crash["episodes"] == 1
    assert after_crash["policy_trials"] > 0

    resume = copy.deepcopy(checkpoints[-1])
    monkeypatch.setattr(runtime_module, "apply_semantic_trajectory_credit", original_credit)
    retry = AgentHarness(catalog, memory=memory)
    retry._finalization_event_key = "api:checkpoint-job"  # noqa: SLF001 - production API contract
    result = retry.run(
        "做一次全局体检，告诉我最值得先处理的问题",
        resume=resume,
        checkpoint_sink=checkpoint_sink,
    )

    after_retry = _final_learning_counts(path)
    assert after_retry["markers"] == 1
    assert after_retry["episodes"] == 1
    assert after_retry["policy_trials"] == after_crash["policy_trials"]
    assert result["autonomy"]["final_learning_commit"]["atomic"] is True
    assert result["autonomy"]["final_learning_commit"]["deduplicated"] is True
    assert isinstance(result.get("policy_credit"), dict)
    assert isinstance(result.get("mechanism_evidence"), dict)

    completed = [row for row in checkpoints if row.get("status") == "completed"]
    assert len(completed) == 1
    final_result = completed[0]["result"]
    assert final_result["policy_credit"] == result["policy_credit"]
    assert final_result["mechanism_evidence"] == result["mechanism_evidence"]
    assert final_result["autonomy"]["final_learning_commit"]["deduplicated"] is True


def _api_snapshot(run_id: str, conversation_id: str) -> dict:
    now = time.time()
    return {
        "run_id": run_id,
        "conversation_id": conversation_id,
        "goal": "finalization key regression",
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


def test_api_runner_binds_job_id_as_stable_finalization_key(monkeypatch, tmp_path):
    store = WorkspaceStore(tmp_path / "api-key.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation("finalization-key", "search")
    run_id = "job-stable-finalization-key"
    snapshot = _api_snapshot(run_id, conversation["id"])
    assert store.reserve_run(
        run_id,
        conversation["id"],
        snapshot["goal"],
        snapshot,
        owner_id=api_module.WORKER_ID,
        lease_seconds=api_module.RUN_LEASE_SECONDS,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = snapshot
    api_module._PERSIST_META.pop(run_id, None)

    seen: list[str] = []

    def run(self, text, **kwargs):
        seen.append(str(getattr(self, "_finalization_event_key", "")))
        return {"answer": "ok"}

    monkeypatch.setattr(api_module.AgentHarness, "run", run)
    runner = api_module.harness.fork()
    asyncio.run(
        api_module._execute(
            run_id,
            conversation["id"],
            snapshot["goal"],
            runner,
            catalog_revision=api_module.CATALOG_REVISION,
        )
    )

    assert seen == [f"api:{run_id}"]
    assert not hasattr(runner, "_finalization_event_key")


def test_completed_checkpoint_recovery_repairs_legacy_public_finalization_before_publish(
    monkeypatch,
    tmp_path,
):
    store = WorkspaceStore(tmp_path / "legacy-completed.db")
    monkeypatch.setattr(api_module, "store", store)
    conversation = store.create_conversation("legacy-completed", "search")
    run_id = "job-legacy-completed-finalization"
    internal_result = {
        "run_id": "run-legacy-internal",
        "answer": "legacy answer",
        "events": [],
        "actions": [],
        "plan": {"mode": "audit"},
        "autonomy": {},
    }
    snapshot = _api_snapshot(run_id, conversation["id"])
    snapshot["checkpoint"] = {
        "status": "completed",
        "run_id": internal_result["run_id"],
        "cycle": 0,
        "result": copy.deepcopy(internal_result),
        "events": [],
    }
    assert store.reserve_run(
        run_id,
        conversation["id"],
        snapshot["goal"],
        snapshot,
        owner_id="dead-worker",
        lease_seconds=1.0,
    )
    now = time.time()
    with store._lock, store._connect() as conn:  # noqa: SLF001 - deterministic recovery fixture
        conn.execute("update runs set lease_until=? where run_id=?", (now - 1.0, run_id))
        conn.commit()

    calls: list[str] = []

    def finalize_result(result: dict) -> dict:
        calls.append(str(result["run_id"]))
        updated = copy.deepcopy(result)
        updated["policy_credit"] = {"method": "legacy-repair", "applied": True}
        updated["mechanism_evidence"] = {"method": "legacy-repair", "recorded": 0}
        return updated

    monkeypatch.setattr(api_module.harness, "finalize_result", finalize_result)
    asyncio.run(api_module._recover_on_startup())

    assert calls == ["run-legacy-internal"]
    saved = store.get_run(run_id)
    assert saved["status"] == "completed"
    assistant = store.assistant_for_job(conversation["id"], run_id)
    assert assistant is not None
    payload = assistant["payload"]
    assert payload["policy_credit"]["method"] == "legacy-repair"
    assert payload["mechanism_evidence"]["method"] == "legacy-repair"
