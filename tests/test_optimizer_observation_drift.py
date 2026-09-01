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


def _paired_rows(
    *,
    recent_scores,
    history_scores,
    recent_feasible=None,
    history_feasible=None,
):
    recent_feasible = recent_feasible or [False, False, True, True]
    history_feasible = history_feasible or [False, False, True, True]
    history_x = [0.10, 0.30, 0.60, 0.90]
    recent_x = [0.12, 0.32, 0.62, 0.92]
    return [
        *[
            _row(x, score, feasible, updated_at=2_000.0)
            for x, score, feasible in zip(recent_x, recent_scores, recent_feasible)
        ],
        *[
            _row(x, score, feasible, updated_at=1_000.0)
            for x, score, feasible in zip(history_x, history_scores, history_feasible)
        ],
    ]


def test_drift_detector_ignores_stable_local_geometry_and_global_level_shift():
    stable = _paired_rows(
        recent_scores=[0.11, 0.31, 0.61, 0.91],
        history_scores=[0.10, 0.30, 0.60, 0.90],
    )
    shifted = _paired_rows(
        recent_scores=[1.10, 1.30, 1.60, 1.90],
        history_scores=[0.10, 0.30, 0.60, 0.90],
    )

    stable_result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=stable,
    )
    shifted_result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=shifted,
    )

    assert stable_result["available"] is True
    assert stable_result["change_detected"] is False
    assert shifted_result["change_detected"] is False
    assert shifted_result["order_inversion_rate"] == 0.0
    assert shifted_result["contrast_shift"] == pytest.approx(0.0)
    assert shifted_result["new_evaluator_calls"] == 0


def test_drift_detector_finds_local_order_inversion_change_point():
    observations = _paired_rows(
        recent_scores=[0.90, 0.60, 0.30, 0.10],
        history_scores=[0.10, 0.30, 0.60, 0.90],
    )

    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=observations,
    )

    assert result["change_detected"] is True
    assert result["primary_signals"] == ["local_order_inversion"]
    assert "local_order_inversion" in result["signals"]
    assert result["recent_rows"] == 4
    assert result["matched_pairs"] == 4
    assert result["match_coverage"] == pytest.approx(1.0)
    assert result["order_inversion_rate"] == pytest.approx(1.0)


def test_drift_detector_uses_score_geometry_as_primary_and_feasibility_as_supporting():
    contrast = _paired_rows(
        recent_scores=[0.30, 0.90, 1.80, 2.70],
        history_scores=[0.10, 0.30, 0.60, 0.90],
    )
    feasibility = _paired_rows(
        recent_scores=[0.11, 0.31, 0.61, 0.91],
        history_scores=[0.10, 0.30, 0.60, 0.90],
        recent_feasible=[True, True, True, True],
        history_feasible=[False, False, False, False],
    )

    contrast_result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=contrast,
    )
    feasibility_result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=feasibility,
    )

    assert contrast_result["change_detected"] is True
    assert "local_contrast_shift" in contrast_result["primary_signals"]
    assert contrast_result["contrast_shift"] >= 0.75

    assert feasibility_result["change_detected"] is False
    assert feasibility_result["primary_signals"] == []
    assert feasibility_result["supporting_signals"] == ["local_feasibility_shift"]
    assert "local_feasibility_shift" in feasibility_result["signals"]
    assert feasibility_result["feasibility_flip_rate"] == pytest.approx(1.0)
    assert feasibility_result["feasibility_density_delta"] == pytest.approx(1.0)


def test_drift_detector_does_not_confuse_region_shift_with_structural_drift():
    observations = [
        _row(0.70, 0.90, True, updated_at=2_000.0),
        _row(0.80, 0.10, False, updated_at=2_000.0),
        _row(0.90, 0.80, True, updated_at=2_000.0),
        _row(1.00, 0.20, False, updated_at=2_000.0),
        _row(0.00, 0.10, False, updated_at=1_000.0),
        _row(0.10, 0.30, False, updated_at=1_000.0),
        _row(0.20, 0.60, True, updated_at=1_000.0),
        _row(0.30, 0.90, True, updated_at=1_000.0),
    ]

    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=observations,
    )

    assert result["available"] is True
    assert result["change_detected"] is False
    assert result["match_coverage"] < 0.75


def test_drift_detector_scans_multiple_cohorts_and_selects_latest_break():
    observations = [
        _row(0.12, 0.90, False, updated_at=3_000.0),
        _row(0.32, 0.60, False, updated_at=3_000.0),
        _row(0.62, 0.30, True, updated_at=3_000.0),
        _row(0.92, 0.10, True, updated_at=3_000.0),
        _row(0.11, 0.11, False, updated_at=2_000.0),
        _row(0.31, 0.31, False, updated_at=2_000.0),
        _row(0.61, 0.61, True, updated_at=2_000.0),
        _row(0.91, 0.91, True, updated_at=2_000.0),
        _row(0.10, 0.10, False, updated_at=1_000.0),
        _row(0.30, 0.30, False, updated_at=1_000.0),
        _row(0.60, 0.60, True, updated_at=1_000.0),
        _row(0.90, 0.90, True, updated_at=1_000.0),
    ]

    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=observations,
    )

    assert result["change_detected"] is True
    assert result["recent_rows"] == 4
    assert result["recent_oldest_at"] == 3_000.0
    assert result["candidate_splits"] >= 2


def _runtime_rows(registry, *, now, drift, concentrated_recent=False):
    dimensions, _ = core._evolution_schema(registry.search.config)
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
    base = asdict(registry.search.config)
    history_scores = [0.10, 0.30, 0.60, 0.90]
    recent_scores = (
        [0.90, 0.60, 0.30, 0.10]
        if drift
        else [0.11, 0.31, 0.61, 0.91]
    )

    rows = []
    for index, (value, score) in enumerate(zip(recent_values, recent_scores)):
        config = dict(base)
        config[continuous.name] = value
        rows.append(
            {
                "config": config,
                "objective": score,
                "feasible": index >= 2,
                "updated_at": now,
                "seen_count": (
                    64 if concentrated_recent and index == 0 else 1
                ),
            }
        )
    for index, (value, score) in enumerate(zip(history_values, history_scores)):
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


def test_runtime_drift_guard_quarantines_old_rows_and_uses_fresh_recent_geometry(
    monkeypatch,
):
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    now = 8_000_000_000.0
    observations, dimensions = _runtime_rows(registry, now=now, drift=True)
    monkeypatch.setattr(
        registry.memory,
        "optimizer_observations",
        lambda *args, **kwargs: list(observations),
    )

    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting

    monkeypatch.setattr(runtime_drift.time, "time", lambda: now)
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: now)

    before = registry._routing_context_without_optimizer_observations("search")
    after = registry._routing_context("search")
    expected = describe_weighted_optimizer_landscape(
        dimensions=dimensions,
        observations=weight_optimizer_observations(
            observations[:4],
            reference_time=now,
        ),
    )
    manifest = registry.inspect_data()["optimizer_meta_router"]
    state = manifest["optimizer_observation_drift_states"]["search"]

    assert state["change_detected"] is True
    assert state["action"] == "recent_only_weighted_geometry"
    assert state["recent_confidence"]["enter_confident"] is True
    assert after.landscape.to_dict() == expected.to_dict()
    assert after.warm_start_rows == before.warm_start_rows
    assert manifest["optimizer_observation_routing_regimes"]["search"] == "weighted"
    assert manifest["optimizer_observation_drift_primary_signals"] == "local_order_or_contrast"
    assert manifest["optimizer_observation_drift_feasibility_role"] == "supporting_only_without_same_config_history"
    assert manifest["optimizer_observation_drift_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_drift_evaluator_calls"] == 0


def test_runtime_drift_guard_falls_back_and_tombstones_prior_weighted_checkpoint(
    monkeypatch,
):
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    now = 9_000_000_000.0
    stable, _ = _runtime_rows(registry, now=now, drift=False)
    drifted, _ = _runtime_rows(
        registry,
        now=now,
        drift=True,
        concentrated_recent=True,
    )
    holder = {"rows": stable}
    monkeypatch.setattr(
        registry.memory,
        "optimizer_observations",
        lambda *args, **kwargs: list(holder["rows"]),
    )

    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    monkeypatch.setattr(runtime_drift.time, "time", lambda: now)
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: now)
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: now)

    first = registry._routing_context("search")
    assert first.landscape.informative is True
    store = registry._optimizer_routing_checkpoint_store
    assert store.read(registry.catalog_key, "search", now=now)["regime"] == "weighted"

    holder["rows"] = drifted
    after = registry._routing_context("search")
    fallback = registry._routing_context_without_optimizer_observations("search")
    checkpoint = store.read(registry.catalog_key, "search", now=now)
    state = registry.inspect_data()["optimizer_meta_router"][
        "optimizer_observation_drift_states"
    ]["search"]

    assert state["change_detected"] is True
    assert state["action"] == "pre_observation_fallback"
    assert state["recent_confidence"]["enter_confident"] is False
    assert after.landscape.to_dict() == fallback.landscape.to_dict()
    assert checkpoint["regime"] == "fallback"
    assert checkpoint["active_weighted"] is False
