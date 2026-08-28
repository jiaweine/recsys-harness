from __future__ import annotations

import asyncio
import copy
import time

import lingjing_harness.api as api_module
from lingjing_harness.domain import Catalog
from lingjing_harness.production import ExposureEvent, RewardSpec
from lingjing_harness.runtime.memory import catalog_fingerprint
from lingjing_harness.sample_data import build_sample_catalog
from lingjing_harness.workspace_identity import workspace_fingerprint


def test_terminal_run_read_replaces_stale_active_snapshot_with_full_durable_result():
    conversation = api_module.store.create_conversation("coherent run", "search")
    run_id = "job-terminal-coherence"
    now = time.time()
    running = {
        "run_id": run_id,
        "conversation_id": conversation["id"],
        "goal": "check",
        "status": "running",
        "events": [],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    api_module.store.delete_run(run_id)
    assert api_module.store.reserve_run(
        run_id,
        conversation["id"],
        "check",
        running,
        owner_id=api_module.WORKER_ID,
        lease_seconds=30,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = copy.deepcopy(running)

    completed = {
        **running,
        "status": "completed",
        "result": {"multimodal": {"context_used": True}, "events": [{"progress": 100}]},
        "updated_at": time.time(),
    }
    api_module.store.save_run(
        run_id,
        conversation["id"],
        "check",
        "completed",
        completed,
        owner_id=api_module.WORKER_ID,
    )

    row = api_module.get_run(run_id)
    assert row["status"] == "completed"
    assert row["result"]["multimodal"]["context_used"] is True
    with api_module.RUN_LOCK:
        assert api_module.RUNS[run_id]["result"] == row["result"]
        api_module.RUNS.pop(run_id, None)
    api_module.store.delete_run(run_id)


def test_active_run_poll_reads_full_snapshot_only_after_terminal_transition(monkeypatch):
    conversation = api_module.store.create_conversation("cheap active polling", "search")
    run_id = "job-active-poll-hot-path"
    now = time.time()
    running = {
        "run_id": run_id,
        "conversation_id": conversation["id"],
        "goal": "poll",
        "status": "running",
        "events": [{"progress": 10}],
        "result": None,
        "created_at": now,
        "updated_at": now,
    }
    api_module.store.delete_run(run_id)
    assert api_module.store.reserve_run(
        run_id,
        conversation["id"],
        "poll",
        running,
        owner_id=api_module.WORKER_ID,
        lease_seconds=30,
    )
    with api_module.RUN_LOCK:
        api_module.RUNS[run_id] = copy.deepcopy(running)

    original_status = api_module.store.run_status
    original_get = api_module.store.get_run
    calls = {"status": 0, "snapshot": 0}

    def counted_status(target: str):
        calls["status"] += 1
        return original_status(target)

    def counted_get(target: str):
        calls["snapshot"] += 1
        return original_get(target)

    monkeypatch.setattr(api_module.store, "run_status", counted_status)
    monkeypatch.setattr(api_module.store, "get_run", counted_get)

    for _ in range(25):
        row = api_module.get_run(run_id)
        assert row["status"] == "running"
        assert row["result"] is None

    assert calls == {"status": 25, "snapshot": 0}

    completed = {
        **running,
        "status": "completed",
        "result": {"answer": "done", "events": [{"progress": 100}]},
        "updated_at": time.time(),
    }
    api_module.store.save_run(
        run_id,
        conversation["id"],
        "poll",
        "completed",
        completed,
        owner_id=api_module.WORKER_ID,
    )

    row = api_module.get_run(run_id)
    assert row["status"] == "completed"
    assert row["result"]["answer"] == "done"
    assert calls == {"status": 26, "snapshot": 1}

    with api_module.RUN_LOCK:
        api_module.RUNS.pop(run_id, None)
    api_module.store.delete_run(run_id)


def test_checkpoint_snapshot_deduplicates_events_and_restores_them_for_resume():
    events = [
        {"phase": "execute", "progress": 20},
        {"phase": "reflect", "progress": 21},
    ]
    row = {
        "run_id": "job-checkpoint-compact",
        "conversation_id": "cv-checkpoint-compact",
        "goal": "compact",
        "status": "running",
        "events": copy.deepcopy(events),
        "result": None,
        "checkpoint": {
            "status": "running",
            "run_id": "run-checkpoint-compact",
            "cycle": 1,
            "events": copy.deepcopy(events),
            "actions": [{"tool": "data.inspect"}],
        },
    }

    compact = api_module._compact_run_snapshot(row)
    assert compact["events"] == events
    assert "events" not in compact["checkpoint"]
    assert row["checkpoint"]["events"] == events

    restored = api_module._inflate_checkpoint(compact)
    assert restored is not None
    assert restored["events"] == events
    assert restored["actions"] == [{"tool": "data.inspect"}]

    terminal = api_module._compact_run_snapshot(
        {**row, "status": "completed", "result": {"answer": "done"}}
    )
    assert "checkpoint" not in terminal


def test_run_persistence_coalesces_decide_and_reflect_writes(monkeypatch):
    run_id = "job-coalesced-write-test"
    row = {
        "run_id": run_id,
        "conversation_id": "cv-coalesced-write-test",
        "goal": "coalesce",
        "status": "running",
        "events": [],
        "result": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    api_module._PERSIST_META.pop(run_id, None)
    snapshots = []

    def fake_save_run(_run_id, _cid, _goal, status, snapshot, **_kwargs):
        snapshots.append(copy.deepcopy(snapshot))
        return status

    monkeypatch.setattr(api_module.store, "save_run", fake_save_run)

    api_module._persist_run(row)
    row["events"].append({"phase": "decide", "progress": 18})
    api_module._persist_run(row)
    row["events"].append({"phase": "execute", "progress": 20})
    api_module._persist_run(row)
    row["events"].append({"phase": "reflect", "progress": 21})
    api_module._persist_run(row)
    row["checkpoint"] = {
        "status": "running",
        "cycle": 1,
        "events": copy.deepcopy(row["events"]),
        "actions": [{"tool": "search.run"}],
    }
    api_module._persist_run(row)

    assert len(snapshots) == 3
    assert snapshots[0]["events"] == []
    assert snapshots[1]["events"][-1]["phase"] == "execute"
    assert snapshots[2]["events"][-1]["phase"] == "reflect"
    assert "events" not in snapshots[2]["checkpoint"]

    row["status"] = "completed"
    row["result"] = {"answer": "done"}
    api_module._persist_run(row)
    assert len(snapshots) == 4
    assert snapshots[-1]["status"] == "completed"
    assert "checkpoint" not in snapshots[-1]
    assert run_id not in api_module._PERSIST_META


def test_completed_checkpoint_recovery_finalizes_without_replaying_harness(monkeypatch):
    conversation = api_module.store.create_conversation("completed checkpoint recovery", "search")
    run_id = "job-completed-checkpoint-recovery"
    now = time.time()
    events = [{"phase": "complete", "title": "done", "detail": "done", "progress": 100}]
    final_result = {
        "run_id": "run-internal-final",
        "answer": "recovered final answer",
        "events": copy.deepcopy(events),
        "durability": {"checkpoint_resume": True},
    }
    snapshot = {
        "run_id": run_id,
        "conversation_id": conversation["id"],
        "goal": "recover completed checkpoint",
        "status": "running",
        "events": copy.deepcopy(events),
        "result": None,
        "attachments": [],
        "attachment_ids": [],
        "allow_network": False,
        "catalog_revision": api_module.CATALOG_REVISION,
        "checkpoint": {
            "status": "completed",
            "run_id": "run-internal-final",
            "cycle": 2,
            "events": copy.deepcopy(events),
            "result": copy.deepcopy(final_result),
        },
        "created_at": now,
        "updated_at": now,
    }
    api_module.store.delete_run(run_id)
    assert api_module.store.reserve_run(
        run_id,
        conversation["id"],
        snapshot["goal"],
        api_module._compact_run_snapshot(snapshot),
        owner_id=api_module.WORKER_ID,
        lease_seconds=30,
    )

    def fail_if_replayed():
        raise AssertionError("completed checkpoint must not fork and replay the harness")

    monkeypatch.setattr(api_module.harness, "fork", fail_if_replayed)
    asyncio.run(api_module._recover_on_startup())

    saved = api_module.store.get_run(run_id)
    assert saved["status"] == "completed"
    assert saved["result"]["answer"] == "recovered final answer"
    assert saved["result"]["job_id"] == run_id
    assert saved["result"]["catalog_revision"] == api_module.CATALOG_REVISION
    assert "checkpoint" not in saved

    messages = api_module.store.list_messages(conversation["id"])
    assistants = [message for message in messages if message["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["payload"]["job_id"] == run_id

    asyncio.run(api_module._recover_on_startup())
    messages = api_module.store.list_messages(conversation["id"])
    assert len([message for message in messages if message["role"] == "assistant"]) == 1
    api_module.store.delete_run(run_id)


def _catalog_with_events(*, reward_click: float = 1.0, requests: int = 1) -> Catalog:
    base = build_sample_catalog()
    item_id = base.items[0].item_id
    events = [
        ExposureEvent(
            request_id=f"r-{index}",
            timestamp=float(100 + index),
            surface="recommend",
            user_id="u-lin",
            item_id=item_id,
            event="click",
            value=1.0,
            propensity=0.5,
            position=1,
        )
        for index in range(requests)
    ]
    return Catalog(
        items=list(base.items),
        interactions=list(base.interactions),
        query_labels=list(base.query_labels),
        events=events,
        reward_spec=RewardSpec(weights={"click": reward_click}),
        name=base.name,
    )


def test_strategy_identity_survives_new_outcomes_but_workspace_revision_does_not():
    older = _catalog_with_events(requests=1)
    newer = _catalog_with_events(requests=2)
    assert catalog_fingerprint(older) == catalog_fingerprint(newer)
    assert workspace_fingerprint(older) != workspace_fingerprint(newer)


def test_reward_contract_change_invalidates_strategy_and_workspace_identity():
    old_goal = _catalog_with_events(reward_click=1.0, requests=2)
    new_goal = _catalog_with_events(reward_click=2.0, requests=2)
    assert catalog_fingerprint(old_goal) != catalog_fingerprint(new_goal)
    assert workspace_fingerprint(old_goal) != workspace_fingerprint(new_goal)
