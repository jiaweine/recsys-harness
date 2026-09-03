from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime import optimizer_observation_memory as observation_memory
from lingjing_harness.runtime import optimizer_routing_checkpoint as checkpoint_runtime
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _runtime_rows(registry, count=4):
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
                break
        if len(configs) >= count:
            break
    assert len(configs) >= count
    return [
        {
            "config": config,
            "objective": 0.2 + 0.1 * index,
            "feasible": index % 2 == 0,
            "source": "checkpoint_future_decision_contract",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs)
    ]


def test_normal_checkpoint_repairs_decision_clock_far_beyond_ttl(tmp_path):
    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(tmp_path / "future-checkpoint-decision-store.db"),
        optimizer_backend="auto",
    )
    store = OptimizerRoutingCheckpointStore(registry.optimizer_meta_memory)
    now = 50_000.0
    future = now + 365.0 * 24.0 * 60.0 * 60.0

    seeded = store.record(
        registry.catalog_key,
        "search",
        regime="weighted",
        evidence_updated_at=now,
        evidence_seen_count=4,
        evidence_rows=4,
        decision_at=future,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )
    assert seeded["recorded"] is True
    assert store.read(registry.catalog_key, "search", now=now)["active_weighted"] is False

    repaired = store.record(
        registry.catalog_key,
        "search",
        regime="weighted",
        evidence_updated_at=now,
        evidence_seen_count=4,
        evidence_rows=4,
        decision_at=now,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    assert repaired["recorded"] is True
    assert repaired["decision_at"] == now
    assert repaired["expires_at"] == now + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS
    assert store.read(registry.catalog_key, "search", now=now)["active_weighted"] is True


def test_routing_refresh_repairs_far_future_checkpoint_decision(tmp_path, monkeypatch):
    path = tmp_path / "future-checkpoint-decision-routing.db"
    now = 60_000.0
    future = now + 365.0 * 24.0 * 60.0 * 60.0

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    monkeypatch.setattr(observation_memory.time, "time", lambda: now)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _runtime_rows(registry),
    )
    observations = registry.memory.optimizer_observations(registry.catalog_key, "search")
    evidence = checkpoint_runtime.optimizer_observation_evidence_clock(observations)
    store = OptimizerRoutingCheckpointStore(registry.optimizer_meta_memory)
    seeded = store.record(
        registry.catalog_key,
        "search",
        regime="weighted",
        decision_at=future,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
        **evidence,
    )
    assert seeded["recorded"] is True

    decision = registry._routing_context("search")
    repaired = store.read(registry.catalog_key, "search", now=now)

    assert decision.landscape.informative is True
    assert repaired["regime"] == "weighted"
    assert repaired["active_weighted"] is True
    assert repaired["decision_at"] == now
    assert repaired["expires_at"] == now + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS
    assert checkpoint_runtime._checkpoint_refreshes(registry)["search"] == now
