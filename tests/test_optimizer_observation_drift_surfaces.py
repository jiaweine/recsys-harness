from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_observation_weighting import (
    describe_weighted_optimizer_landscape,
)
from lingjing_harness.runtime.optimizer_observation_drift import (
    detect_optimizer_observation_drift,
)
from lingjing_harness.runtime.optimizer_observation_weighting import (
    weight_optimizer_observations,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0


def _mixed_rows(*, same_category: bool):
    dimensions = [
        core.EvolutionDimension(
            name="x",
            kind="continuous",
            group="independent",
            low=0.0,
            high=1.0,
        ),
        core.EvolutionDimension(
            name="mode",
            kind="categorical",
            group="independent",
            choices=("a", "b"),
        ),
    ]
    history_x = [0.10, 0.30, 0.60, 0.90]
    recent_x = [0.12, 0.32, 0.62, 0.92]
    history_scores = [0.10, 0.30, 0.60, 0.90]
    recent_scores = [0.90, 0.60, 0.30, 0.10]
    recent_mode = "a" if same_category else "b"
    rows = [
        *[
            {
                "config": {"x": x, "mode": recent_mode},
                "objective": score,
                "feasible": index >= 2,
                "updated_at": 2_000.0,
                "seen_count": 1,
            }
            for index, (x, score) in enumerate(zip(recent_x, recent_scores))
        ],
        *[
            {
                "config": {"x": x, "mode": "a"},
                "objective": score,
                "feasible": index >= 2,
                "updated_at": 1_000.0,
                "seen_count": 1,
            }
            for index, (x, score) in enumerate(zip(history_x, history_scores))
        ],
    ]
    return dimensions, rows


def test_mixed_genome_drift_requires_local_categorical_agreement():
    dimensions, same_category = _mixed_rows(same_category=True)
    _, changed_category = _mixed_rows(same_category=False)

    local = detect_optimizer_observation_drift(
        dimensions=dimensions,
        observations=same_category,
    )
    separated = detect_optimizer_observation_drift(
        dimensions=dimensions,
        observations=changed_category,
    )

    assert local["change_detected"] is True
    assert local["match_coverage"] == pytest.approx(1.0)
    assert separated["change_detected"] is False
    assert separated["match_coverage"] == pytest.approx(0.0)
    assert separated["new_evaluator_calls"] == 0


def _surface_rows(registry, surface: str, *, now: float):
    engine = registry.search if surface == "search" else registry.recommend
    dimensions, _ = core._evolution_schema(engine.config)
    continuous = next(
        dimension
        for dimension in dimensions
        if str(getattr(dimension, "kind", "")) == "continuous"
    )
    low = float(continuous.low)
    high = float(continuous.high)
    width = max(1e-9, high - low)
    history_values = [
        low + width * fraction for fraction in (0.15, 0.35, 0.60, 0.85)
    ]
    recent_values = [
        min(high, value + width * 0.02) for value in history_values
    ]
    base = asdict(engine.config)
    rows = []
    for index, (value, score) in enumerate(
        zip(recent_values, (0.90, 0.60, 0.30, 0.10))
    ):
        config = dict(base)
        config[continuous.name] = value
        rows.append(
            {
                "config": config,
                "objective": score,
                "feasible": index >= 2,
                "updated_at": now,
                "seen_count": 1,
            }
        )
    for index, (value, score) in enumerate(
        zip(history_values, (0.10, 0.30, 0.60, 0.90))
    ):
        config = dict(base)
        config[continuous.name] = value
        rows.append(
            {
                "config": config,
                "objective": score,
                "feasible": index >= 2,
                "updated_at": now - DAY,
                "seen_count": 1,
            }
        )
    return rows, dimensions


def test_recommend_surface_uses_recent_only_geometry_and_persists_final_regime(monkeypatch):
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    now = 10_000_000_000.0
    observations, dimensions = _surface_rows(registry, "recommend", now=now)

    def observations_for_surface(catalog_key, surface, **kwargs):
        del catalog_key, kwargs
        return list(observations) if surface == "recommend" else []

    monkeypatch.setattr(registry.memory, "optimizer_observations", observations_for_surface)

    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    monkeypatch.setattr(runtime_drift.time, "time", lambda: now)
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: now)
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: now)

    pre_observation = registry._routing_context_without_optimizer_observations("recommend")
    routed = registry._routing_context("recommend")
    expected = describe_weighted_optimizer_landscape(
        dimensions=dimensions,
        observations=weight_optimizer_observations(
            observations[:4],
            reference_time=now,
        ),
    )
    manifest = registry.inspect_data()["optimizer_meta_router"]
    state = manifest["optimizer_observation_drift_states"]["recommend"]
    checkpoint = registry._optimizer_routing_checkpoint_store.read(
        registry.catalog_key,
        "recommend",
        now=now,
    )

    assert state["change_detected"] is True
    assert state["action"] == "recent_only_weighted_geometry"
    assert state["recent_confidence"]["enter_confident"] is True
    assert routed.landscape.to_dict() == expected.to_dict()
    assert routed.warm_start_rows == pre_observation.warm_start_rows
    assert manifest["optimizer_observation_routing_regimes"]["recommend"] == "weighted"
    assert checkpoint["regime"] == "weighted"
    assert checkpoint["active_weighted"] is True
    assert manifest["optimizer_observation_drift_evaluator_calls"] == 0
