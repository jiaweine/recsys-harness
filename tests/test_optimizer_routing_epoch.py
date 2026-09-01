from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_routing_epoch import (
    localize_routing_epoch_seen_counts,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _runtime_configs(registry, count=8):
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


def _rows(configs, feasible, *, score_offset=0.0, source="epoch_contract_evaluator"):
    return [
        {
            "config": config,
            "objective": score_offset + 0.2 + 0.1 * index,
            "feasible": label,
            "source": source,
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, (config, label) in enumerate(zip(configs, feasible))
    ]


def test_checkpoint_schema_migrates_existing_rows_without_losing_regime(tmp_path):
    path = tmp_path / "routing-epoch-migration.db"
    memory = AgentMemory(path)
    meta = OptimizerMetaMemory(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute(
                """
                create table agent_optimizer_routing_checkpoint(
                  catalog_key text not null,
                  domain text not null,
                  regime text not null check(regime in ('weighted','fallback')),
                  evidence_updated_at real not null check(evidence_updated_at >= 0),
                  evidence_seen_count integer not null check(evidence_seen_count >= 0),
                  evidence_rows integer not null check(evidence_rows >= 0),
                  decision_at real not null,
                  expires_at real not null,
                  primary key(catalog_key,domain)
                )
                """
            )
            connection.execute(
                """
                insert into agent_optimizer_routing_checkpoint(
                  catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                  evidence_rows,decision_at,expires_at
                ) values(?,?,?,?,?,?,?,?)
                """,
                ("catalog", "search", "weighted", 1_000.0, 4, 4, 1_000.0, 9_999.0),
            )
            connection.commit()
        finally:
            memory._close(connection)

    store = OptimizerRoutingCheckpointStore(meta)
    checkpoint = store.read("catalog", "search", now=2_000.0)

    assert checkpoint["regime"] == "weighted"
    assert checkpoint["evidence_epoch"] == 0
    assert checkpoint["epoch_started_at"] == 0.0
    assert checkpoint["active_weighted"] is True


def test_same_boundary_contention_advances_durable_epoch_once(tmp_path):
    path = tmp_path / "routing-epoch-contention.db"
    stores = [
        OptimizerRoutingCheckpointStore(OptimizerMetaMemory(AgentMemory(path)))
        for _ in range(8)
    ]

    def write(index):
        return stores[index].record(
            "catalog",
            "search",
            regime="weighted",
            evidence_updated_at=2_000.0,
            evidence_seen_count=8,
            evidence_rows=4,
            epoch_started_at=2_000.0,
            decision_at=2_000.0 + index,
            ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(8)))

    checkpoint = stores[0].read("catalog", "search", now=2_100.0)
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["epoch_started_at"] == 2_000.0


def test_routing_epoch_checkpoint_remains_backend_scoped():
    base = AgentMemory()
    scoped = BackendScopedMemory(base, search_scope="semantic-epoch-a")
    scoped_store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(scoped))
    base_store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(base))

    scoped_store.record(
        "catalog",
        "search",
        regime="weighted",
        evidence_updated_at=2_000.0,
        evidence_seen_count=4,
        evidence_rows=4,
        epoch_started_at=2_000.0,
        decision_at=2_000.0,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    scoped_checkpoint = scoped_store.read("catalog", "search", now=2_100.0)
    assert scoped_checkpoint["evidence_epoch"] == 1
    assert scoped_checkpoint["epoch_started_at"] == 2_000.0
    assert base_store.read("catalog", "search", now=2_100.0) is None


def test_epoch_local_seen_count_uses_only_paid_history_after_boundary():
    observation = {
        "config": {"x": 0.25},
        "objective": 0.5,
        "feasible": True,
        "seen_count": 9,
        "updated_at": 2_100.0,
    }
    history = [
        {"config": {"x": 0.25}, "observed_at": 1_000.0},
        {"config": {"x": 0.25}, "observed_at": 1_100.0},
        {"config": {"x": 0.25}, "observed_at": 2_000.0},
        {"config": {"x": 0.25}, "observed_at": 2_100.0},
    ]

    localized = localize_routing_epoch_seen_counts(
        [observation],
        history,
        epoch_started_at=2_000.0,
    )

    assert observation["seen_count"] == 9
    assert localized[0]["seen_count"] == 2
    assert localized[0]["routing_epoch_seen_count"] == 2


def test_confirmed_drift_fences_weighting_and_history_across_restart_and_next_epoch(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    path = tmp_path / "routing-epoch-runtime.db"
    clock = {"now": 1_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(registry, count=8)
    current_configs = configs[:4]
    stale_configs = configs[4:8]

    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        [
            *_rows(current_configs, [False, False, True, True]),
            *_rows(stale_configs, [False, False, True, True], score_offset=0.05),
        ],
    )
    clock["now"] = 2_000.0
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(current_configs, [True, True, True, True], score_offset=0.01),
    )

    first = registry._routing_context("search")
    checkpoint = registry._optimizer_routing_checkpoint_store.read(
        registry.catalog_key,
        "search",
        now=clock["now"],
    )
    first_state = registry.inspect_data()["optimizer_meta_router"][
        "optimizer_observation_drift_states"
    ]["search"]
    lifetime_latest = registry.memory.optimizer_observations(
        registry.catalog_key,
        "search",
    )

    assert first_state["change_detected"] is True
    assert "same_config_feasibility_shift" in first_state["primary_signals"]
    assert first_state["routing_epoch_advance_requested_at"] == 2_000.0
    assert first_state["routing_epoch_recent_seen_count"] == 4
    assert first_state["recent_confidence"]["total_weight"] == pytest.approx(4.0)
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["epoch_started_at"] == 2_000.0
    assert checkpoint["evidence_seen_count"] == 4
    assert {row["seen_count"] for row in lifetime_latest[:4]} == {2}
    assert first.landscape.feasible_density == pytest.approx(1.0)

    second = registry._routing_context("search")
    manifest = registry.inspect_data()["optimizer_meta_router"]
    second_state = manifest["optimizer_observation_drift_states"]["search"]

    assert second_state["change_detected"] is False
    assert second_state["evidence_epoch"] == 1
    assert second_state["epoch_started_at"] == 2_000.0
    assert second_state["epoch_observation_rows"] == 4
    assert second_state["epoch_history_rows"] == 4
    assert second_state["epoch_filtered_observation_rows"] == 4
    assert second_state["epoch_filtered_history_rows"] == 8
    assert second.landscape.feasible_density == pytest.approx(1.0)
    assert manifest["optimizer_observation_routing_epoch"] == "durable_checkpoint_change_point_fence"
    assert manifest["optimizer_observation_routing_epoch_boundary"] == "confirmed_recent_oldest_at"
    assert manifest["optimizer_observation_routing_epoch_states"]["search"] == {
        "evidence_epoch": 1,
        "epoch_started_at": 2_000.0,
    }
    assert manifest["optimizer_observation_routing_epoch_seen_count"] == (
        "bounded_paid_history_since_epoch_boundary"
    )
    assert manifest["optimizer_observation_routing_epoch_seen_count_scope"] == (
        "routing_context_only"
    )
    assert manifest["optimizer_observation_routing_epoch_seen_count_authority"] == (
        "routing_descriptor_only"
    )
    assert manifest["optimizer_observation_routing_epoch_seen_count_evaluator_calls"] == 0
    assert manifest["optimizer_observation_routing_epoch_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_routing_epoch_evaluator_calls"] == 0

    restarted = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    restarted_context = restarted._routing_context("search")
    restarted_state = restarted.inspect_data()["optimizer_meta_router"][
        "optimizer_observation_drift_states"
    ]["search"]
    restarted_checkpoint = restarted._optimizer_routing_checkpoint_store.read(
        restarted.catalog_key,
        "search",
        now=clock["now"],
    )
    assert restarted_state["change_detected"] is False
    assert restarted_state["evidence_epoch"] == 1
    assert restarted_state["epoch_filtered_observation_rows"] == 4
    assert restarted_checkpoint["evidence_seen_count"] == 4
    assert restarted_context.landscape.feasible_density == pytest.approx(1.0)

    clock["now"] = 3_000.0
    restarted.memory.record_optimizer_observations(
        restarted.catalog_key,
        "search",
        _rows(current_configs, [False, False, True, True], score_offset=0.02),
    )
    third = restarted._routing_context("search")
    checkpoint = restarted._optimizer_routing_checkpoint_store.read(
        restarted.catalog_key,
        "search",
        now=clock["now"],
    )
    third_state = restarted.inspect_data()["optimizer_meta_router"][
        "optimizer_observation_drift_states"
    ]["search"]
    lifetime_latest = restarted.memory.optimizer_observations(
        restarted.catalog_key,
        "search",
    )

    assert third_state["change_detected"] is True
    assert "same_config_feasibility_shift" in third_state["primary_signals"]
    assert third_state["routing_epoch_advance_requested_at"] == 3_000.0
    assert third_state["routing_epoch_recent_seen_count"] == 4
    assert third_state["recent_confidence"]["total_weight"] == pytest.approx(4.0)
    assert checkpoint["evidence_epoch"] == 2
    assert checkpoint["epoch_started_at"] == 3_000.0
    assert checkpoint["evidence_seen_count"] == 4
    assert {row["seen_count"] for row in lifetime_latest[:4]} == {3}
    assert third.landscape.feasible_density == pytest.approx(0.5)
