from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS,
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _configs(registry, count=4):
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


def _rows(registry):
    return [
        {
            "config": config,
            "objective": 0.2 + 0.1 * index,
            "feasible": True,
            "source": "canonical_checkpoint_fence_contract",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(_configs(registry))
    ]


def test_legacy_future_checkpoint_refresh_is_not_a_concurrent_row_change(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    path = tmp_path / "canonical-checkpoint-fence.db"
    decision_at = 80_000_000.0
    future = decision_at + 365.0 * 24.0 * 60.0 * 60.0
    clock = {"now": decision_at}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    writer = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _rows(writer)
    recorded = writer.memory.record_optimizer_observations(
        writer.catalog_key,
        "search",
        rows,
    )
    assert recorded["history_rows"] == 4

    store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(writer.memory))
    scoped_catalog_key = store._scoped_catalog_key(writer.catalog_key, "search")
    with writer.memory._lock:
        connection = writer.memory._connect()
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
            writer.memory._close(connection)

    clock["now"] = decision_at + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_REFRESH_SECONDS + 1.0
    restarted = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    context = restarted._routing_context("search")
    manifest = restarted.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]

    with restarted.memory._lock:
        connection = restarted.memory._connect()
        try:
            raw = connection.execute(
                """
                select regime,evidence_updated_at,evidence_seen_count,evidence_rows,
                       evidence_epoch,epoch_started_at,decision_at,expires_at
                from agent_optimizer_routing_checkpoint
                where catalog_key=? and domain='search'
                """,
                (scoped_catalog_key,),
            ).fetchone()
        finally:
            restarted.memory._close(connection)

    assert context.landscape.informative is True
    assert fence["status"] == "validated"
    assert fence["reason"] == "routing_fences_current"
    assert raw["regime"] == "weighted"
    assert raw["evidence_updated_at"] == decision_at
    assert raw["evidence_seen_count"] == 4
    assert raw["evidence_rows"] == 4
    assert raw["evidence_epoch"] == 1
    assert raw["epoch_started_at"] == clock["now"]
    assert raw["decision_at"] == clock["now"]
    assert raw["expires_at"] == (
        clock["now"] + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS
    )
