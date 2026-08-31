from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_meta import describe_optimizer_landscape
from lingjing_harness.algorithms.optimizer_observation_weighting import (
    describe_weighted_optimizer_landscape,
)
from lingjing_harness.runtime.optimizer_observation_weighting import (
    OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP,
    OPTIMIZER_OBSERVATION_RECENCY_FLOOR,
    optimizer_observation_routing_weight,
    weight_optimizer_observations,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0


def _dimension():
    return core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
    )


def _row(x, score, feasible, *, updated_at, seen_count=1):
    return {
        "config": {"x": x},
        "objective": score,
        "feasible": feasible,
        "updated_at": updated_at,
        "seen_count": seen_count,
    }


def _durable_search_observations(registry, count=8):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    seen = set()
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            key = repr(sorted(config.items()))
            if key in seen:
                continue
            seen.add(key)
            configs.append(config)
            if len(configs) >= count:
                break
        if len(configs) >= count:
            break
    assert len(configs) >= count
    return [
        {
            "config": config,
            "objective": 0.2 + 0.05 * index,
            "feasible": index >= count // 2,
            "source": "paid_test",
            "generation": index,
            "feasibility_basis": "search_discovery_robustness_guardrails",
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs[:count])
    ]


def test_recent_boundary_observations_dominate_stale_equal_count_history():
    now = 1_000_000_000.0
    observations = [
        _row(0.1, 0.1, False, updated_at=now - 56 * DAY),
        _row(0.2, 0.2, False, updated_at=now - 56 * DAY),
        _row(0.3, 0.3, False, updated_at=now - 56 * DAY),
        _row(0.4, 0.4, False, updated_at=now - 56 * DAY),
        _row(0.6, 0.6, True, updated_at=now),
        _row(0.7, 0.7, True, updated_at=now),
        _row(0.8, 0.8, True, updated_at=now),
        _row(0.9, 0.9, True, updated_at=now),
    ]

    unweighted = describe_optimizer_landscape(
        dimensions=[_dimension()], observations=observations
    )
    weighted = describe_weighted_optimizer_landscape(
        dimensions=[_dimension()],
        observations=weight_optimizer_observations(observations, reference_time=now),
    )

    assert unweighted.feasible_density == 0.5
    assert weighted.feasible_density == pytest.approx(8.0 / 9.0)
    assert weighted.feasible_density_bucket == "dense"
    assert weighted.to_dict()["new_evaluator_calls"] == 0


def test_repeated_evidence_has_bounded_logarithmic_influence():
    now = 2_000_000_000.0
    observations = [
        _row(0.1, 0.1, False, updated_at=now, seen_count=16),
        _row(0.4, 0.4, True, updated_at=now),
        _row(0.7, 0.7, True, updated_at=now),
        _row(0.9, 0.9, True, updated_at=now),
    ]
    weighted_rows = weight_optimizer_observations(observations, reference_time=now)
    descriptor = describe_weighted_optimizer_landscape(
        dimensions=[_dimension()], observations=weighted_rows
    )

    assert weighted_rows[0]["routing_evidence_weight"] == OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP
    assert descriptor.feasible_density == pytest.approx(3.0 / 5.0)


def test_stale_repeated_observation_cannot_escape_recency_floor_and_evidence_cap():
    now = 3_000_000_000.0
    weight = optimizer_observation_routing_weight(
        {"updated_at": now - 365 * DAY, "seen_count": 1_000_000},
        reference_time=now,
    )

    assert weight["routing_recency_weight"] == OPTIMIZER_OBSERVATION_RECENCY_FLOOR
    assert weight["routing_evidence_weight"] == OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP
    assert weight["routing_weight"] == pytest.approx(
        OPTIMIZER_OBSERVATION_RECENCY_FLOOR * OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP
    )


def test_weighting_is_transient_and_does_not_mutate_durable_rows():
    now = 4_000_000_000.0
    observations = [_row(0.2, 0.3, True, updated_at=now)]
    weighted = weight_optimizer_observations(observations, reference_time=now)

    assert "routing_weight" not in observations[0]
    assert weighted[0]["routing_weight"] == 1.0
    assert weighted[0] is not observations[0]


def test_runtime_router_uses_weighted_durable_geometry_without_warm_start_pollution():
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    before = registry._routing_context("search")
    observations = _durable_search_observations(registry, 8)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        observations,
    )

    now = 5_000_000_000.0
    with registry.memory._lock:
        conn = registry.memory._connect()
        try:
            conn.execute(
                "update agent_optimizer_observations set updated_at=? where catalog_key=? and domain='search' and feasible=0",
                (now - 56 * DAY, registry.catalog_key),
            )
            conn.execute(
                "update agent_optimizer_observations set updated_at=? where catalog_key=? and domain='search' and feasible=1",
                (now, registry.catalog_key),
            )
            conn.commit()
        finally:
            registry.memory._close(conn)

    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting

    original_time = runtime_weighting.time.time
    runtime_weighting.time.time = lambda: now
    try:
        raw = registry.memory.optimizer_observations(registry.catalog_key, "search")
        unweighted = describe_optimizer_landscape(
            dimensions=core._evolution_schema(registry.search.config)[0],
            observations=raw,
        )
        after = registry._routing_context("search")
    finally:
        runtime_weighting.time.time = original_time

    manifest = registry.inspect_data()["optimizer_meta_router"]
    assert unweighted.feasible_density == 0.5
    assert after.landscape.feasible_density == pytest.approx(8.0 / 9.0)
    assert after.warm_start_rows == before.warm_start_rows
    assert after.landscape.to_dict()["new_evaluator_calls"] == 0
    assert manifest["optimizer_observation_weighting"] == "recency_half_life_and_log_evidence"
    assert manifest["optimizer_observation_weighting_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_weighting_evaluator_calls"] == 0
