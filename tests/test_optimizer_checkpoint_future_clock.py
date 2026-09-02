from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0
BASIS = "search_discovery_robustness_guardrails"


def _store(tmp_path, name: str):
    memory = AgentMemory(tmp_path / name)
    return memory, OptimizerRoutingCheckpointStore(OptimizerMetaMemory(memory))


def _runtime_configs(registry, count=4):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    seen = set()
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            marker = repr(sorted(config.items()))
            if marker in seen:
                continue
            seen.add(marker)
            configs.append(config)
            if len(configs) >= count:
                return configs
    assert len(configs) >= count
    return configs


def _runtime_rows(registry):
    return [
        {
            "config": config,
            "objective": 0.2 + 0.1 * index,
            "feasible": index % 2 == 0,
            "source": "future_checkpoint_restart_contract",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(_runtime_configs(registry))
    ]


def test_checkpoint_cannot_persist_evidence_ahead_of_decision_clock(tmp_path):
    _, store = _store(tmp_path, "future-checkpoint-write.db")
    now = 10_000.0
    future = now + 365.0 * DAY

    checkpoint = store.record(
        "catalog",
        "search",
        regime="weighted",
        evidence_updated_at=future,
        evidence_seen_count=4,
        evidence_rows=4,
        epoch_started_at=future,
        decision_at=now,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    assert checkpoint["recorded"] is True
    assert checkpoint["evidence_updated_at"] == now
    assert checkpoint["epoch_started_at"] == now
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["decision_at"] == now


def test_normal_decision_repairs_legacy_future_skewed_checkpoint(tmp_path):
    memory, store = _store(tmp_path, "future-checkpoint-repair.db")
    now = 20_000.0
    future = now + 365.0 * DAY
    scoped_catalog_key = store._scoped_catalog_key("catalog", "search")

    # Simulate a checkpoint persisted by the pre-fix runtime, where durable
    # observation wall clock was incorrectly allowed to lead the caller clock.
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute(
                """
                insert into agent_optimizer_routing_checkpoint(
                  catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                  evidence_rows,evidence_epoch,epoch_started_at,decision_at,expires_at
                ) values(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scoped_catalog_key,
                    "search",
                    "weighted",
                    future,
                    4,
                    4,
                    1,
                    future,
                    now,
                    now + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
                ),
            )
            connection.commit()
        finally:
            memory._close(connection)

    repaired = store.record(
        "catalog",
        "search",
        regime="fallback",
        evidence_updated_at=now + 100.0,
        evidence_seen_count=8,
        evidence_rows=4,
        epoch_started_at=now + 100.0,
        decision_at=now + 100.0,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    assert repaired["recorded"] is True
    assert repaired["regime"] == "fallback"
    assert repaired["evidence_updated_at"] == now + 100.0
    assert repaired["evidence_seen_count"] == 8
    assert repaired["epoch_started_at"] == now + 100.0
    assert repaired["evidence_epoch"] == 1
    assert repaired["decision_at"] == now + 100.0


def test_restart_normalizes_legacy_future_checkpoint_before_epoch_filtering(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    path = tmp_path / "future-checkpoint-restart.db"
    clock = {"now": 30_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _runtime_rows(registry)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        rows,
    )

    store = OptimizerRoutingCheckpointStore(registry.optimizer_meta_memory)
    decision_at = clock["now"]
    future = decision_at + 365.0 * DAY
    scoped_catalog_key = store._scoped_catalog_key(registry.catalog_key, "search")
    with registry.memory._lock:
        connection = registry.memory._connect()
        try:
            connection.execute(
                """
                insert into agent_optimizer_routing_checkpoint(
                  catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                  evidence_rows,evidence_epoch,epoch_started_at,decision_at,expires_at
                ) values(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scoped_catalog_key,
                    "search",
                    "weighted",
                    future,
                    4,
                    4,
                    1,
                    future,
                    decision_at,
                    decision_at + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
                ),
            )
            connection.commit()
        finally:
            registry.memory._close(connection)

    clock["now"] = decision_at + 100.0
    restarted = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    context = restarted._routing_context("search")
    checkpoint = restarted._optimizer_routing_checkpoint_store.read(
        restarted.catalog_key,
        "search",
        now=clock["now"],
    )
    manifest = restarted.inspect_data()["optimizer_meta_router"]

    assert context.landscape.informative is True
    assert checkpoint["evidence_updated_at"] == decision_at
    assert checkpoint["epoch_started_at"] == decision_at
    assert checkpoint["evidence_epoch"] == 1
    assert manifest["optimizer_observation_routing_epoch_states"]["search"] == {
        "evidence_epoch": 1,
        "epoch_started_at": decision_at,
    }


def test_restart_does_not_restore_checkpoint_decided_ahead_of_caller_clock(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    path = tmp_path / "future-checkpoint-decision.db"
    decision_at = 40_000_000.0
    clock = {"now": decision_at - 5.0 * DAY}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _runtime_rows(registry)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        rows[:1],
    )
    clock["now"] = decision_at
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        rows[1:],
    )

    store = OptimizerRoutingCheckpointStore(registry.optimizer_meta_memory)
    checkpoint = store.record(
        registry.catalog_key,
        "search",
        regime="weighted",
        evidence_updated_at=decision_at,
        evidence_seen_count=4,
        evidence_rows=4,
        epoch_started_at=0.0,
        decision_at=decision_at,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )
    assert checkpoint["recorded"] is True

    clock["now"] = decision_at - 100.0
    restarted = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    context = restarted._routing_context("search")
    restored = restarted._optimizer_routing_checkpoint_store.read(
        restarted.catalog_key,
        "search",
        now=clock["now"],
    )

    assert restored["decision_at"] == decision_at
    assert restored["active_weighted"] is False
    assert context.landscape.informative is False
