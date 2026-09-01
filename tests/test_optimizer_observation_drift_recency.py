import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime.optimizer_observation_drift import (
    detect_optimizer_observation_drift,
)


def _dimension():
    return core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
    )


def _row(x, score, *, updated_at):
    return {
        "config": {"x": x},
        "objective": score,
        "feasible": True,
        "updated_at": updated_at,
        "seen_count": 1,
    }


def test_drift_detector_prefers_latest_qualifying_break_over_older_stronger_break():
    # The newest regime is a real but weaker structural change (4/6 pairwise
    # inversions). The preceding regime is a full inversion versus the oldest
    # regime. Change-point selection must isolate the newest regime instead of
    # letting the older severity=1.0 break absorb both recent regimes.
    observations = [
        _row(0.13, 0.60, updated_at=3_000.0),
        _row(0.33, 0.10, updated_at=3_000.0),
        _row(0.63, 0.30, updated_at=3_000.0),
        _row(0.93, 0.90, updated_at=3_000.0),
        _row(0.12, 0.90, updated_at=2_000.0),
        _row(0.32, 0.60, updated_at=2_000.0),
        _row(0.62, 0.30, updated_at=2_000.0),
        _row(0.92, 0.10, updated_at=2_000.0),
        _row(0.10, 0.10, updated_at=1_000.0),
        _row(0.30, 0.30, updated_at=1_000.0),
        _row(0.60, 0.60, updated_at=1_000.0),
        _row(0.90, 0.90, updated_at=1_000.0),
    ]

    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=observations,
    )

    assert result["change_detected"] is True
    assert result["recent_rows"] == 4
    assert result["recent_oldest_at"] == 3_000.0
    assert result["order_inversion_rate"] == pytest.approx(2.0 / 3.0)
    assert result["severity"] == pytest.approx(2.0 / 3.0)
    assert result["candidate_splits"] == 2
    assert result["new_evaluator_calls"] == 0
