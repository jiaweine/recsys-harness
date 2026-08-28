from __future__ import annotations

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
