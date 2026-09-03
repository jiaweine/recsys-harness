from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0
BASIS = "search_discovery_robustness_guardrails"


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
            "source": "future_observation_recency_anchor_contract",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(_runtime_configs(registry))
    ]


def _anchor_rows(memory):
    with memory._lock:
        connection = memory._connect()
        try:
            rows = connection.execute(
                """
                select observation_id,anchor_at
                from agent_optimizer_observation_recency_anchor
                order by observation_id
                """
            ).fetchall()
        finally:
            memory._close(connection)
    return [(int(row["observation_id"]), float(row["anchor_at"])) for row in rows]


def test_future_paid_observations_age_from_first_local_view_across_restarts(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "future-observation-recency-anchor.db"
    clock = {"now": 80_000_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _runtime_rows(registry)
    local_first_seen_at = clock["now"]
    writer_future = local_first_seen_at + 365.0 * DAY

    # The paid writer jumps far ahead, then the routing caller observes the same
    # durable SQLite database after its wall clock returns to the local timeline.
    clock["now"] = writer_future
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        rows,
    )
    raw = registry.memory.optimizer_observations(registry.catalog_key, "search")
    assert len(raw) == 4
    assert {row["updated_at"] for row in raw} == {writer_future}

    clock["now"] = local_first_seen_at
    first = registry._routing_context("search")
    first_checkpoint = OptimizerRoutingCheckpointStore(
        registry.optimizer_meta_memory
    ).read(
        registry.catalog_key,
        "search",
        now=clock["now"],
    )
    assert first.landscape.informative is True
    assert first_checkpoint["regime"] == "weighted"

    # The same paid commit version must age from the first local observation,
    # rather than receiving a new age=0 clamp on every later routing call.
    clock["now"] = local_first_seen_at + 56.0 * DAY
    aged_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    aged = aged_registry._routing_context("search")
    aged_checkpoint = OptimizerRoutingCheckpointStore(
        aged_registry.optimizer_meta_memory
    ).read(
        aged_registry.catalog_key,
        "search",
        now=clock["now"],
    )
    assert aged.landscape.informative is False
    assert aged_checkpoint["regime"] == "fallback"

    # Reaching the writer's original future timestamp must not resurrect evidence
    # that has already aged for a year on the caller timeline.
    clock["now"] = writer_future
    catchup_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    catchup = catchup_registry._routing_context("search")
    assert catchup.landscape.informative is False


def test_future_anchor_can_only_move_to_an_earlier_caller_clock(tmp_path, monkeypatch):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "future-observation-anchor-monotone.db"
    clock = {"now": 90_000_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _runtime_rows(registry)
    local_first_seen_at = clock["now"]
    writer_future = local_first_seen_at + 365.0 * DAY

    clock["now"] = writer_future
    registry.memory.record_optimizer_observations(registry.catalog_key, "search", rows)

    clock["now"] = local_first_seen_at
    registry._routing_context("search")
    first_anchors = _anchor_rows(registry.memory)
    assert len(first_anchors) == 4
    assert {anchor for _, anchor in first_anchors} == {local_first_seen_at}

    earlier = local_first_seen_at - 100.0
    clock["now"] = earlier
    rollback_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rollback_registry._routing_context("search")
    repaired_anchors = _anchor_rows(rollback_registry.memory)
    assert [row[0] for row in repaired_anchors] == [row[0] for row in first_anchors]
    assert {anchor for _, anchor in repaired_anchors} == {earlier}

    clock["now"] = local_first_seen_at + 100.0
    later_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    later_registry._routing_context("search")
    assert _anchor_rows(later_registry.memory) == repaired_anchors


def test_new_paid_commit_version_gets_a_fresh_anchor(tmp_path, monkeypatch):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "future-observation-anchor-new-version.db"
    clock = {"now": 100_000_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    rows = _runtime_rows(registry)
    local_first_seen_at = clock["now"]
    first_writer_future = local_first_seen_at + 365.0 * DAY

    clock["now"] = first_writer_future
    registry.memory.record_optimizer_observations(registry.catalog_key, "search", rows)
    clock["now"] = local_first_seen_at
    assert registry._routing_context("search").landscape.informative is True
    first_anchors = _anchor_rows(registry.memory)
    assert len(first_anchors) == 4
    assert {anchor for _, anchor in first_anchors} == {local_first_seen_at}

    refreshed_at = local_first_seen_at + 56.0 * DAY
    clock["now"] = refreshed_at
    aged_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    assert aged_registry._routing_context("search").landscape.informative is False

    second_writer_future = first_writer_future + DAY
    clock["now"] = second_writer_future
    aged_registry.memory.record_optimizer_observations(
        aged_registry.catalog_key,
        "search",
        rows,
    )
    raw = aged_registry.memory.optimizer_observations(aged_registry.catalog_key, "search")
    assert {row["updated_at"] for row in raw} == {second_writer_future}

    clock["now"] = refreshed_at
    refreshed_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    assert refreshed_registry._routing_context("search").landscape.informative is True

    anchors = _anchor_rows(refreshed_registry.memory)
    assert len(anchors) == 8
    assert sum(anchor == local_first_seen_at for _, anchor in anchors) == 4
    assert sum(anchor == refreshed_at for _, anchor in anchors) == 4
