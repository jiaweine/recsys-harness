from __future__ import annotations

import pytest

from lingjing_harness.runtime import optimizer_observation_weighting as weighting


DAY = 24.0 * 60.0 * 60.0


def _row(index: int, *, updated_at: float) -> dict[str, object]:
    return {
        "config": {"x": index / 10.0},
        "objective": 0.2 + 0.1 * index,
        "feasible": index % 2 == 0,
        "updated_at": updated_at,
        "seen_count": 1,
    }


def test_future_timestamp_cannot_advance_the_entire_routing_reference_clock(monkeypatch):
    now = 8_000_000_000.0
    monkeypatch.setattr(weighting.time, "time", lambda: now)
    observations = [
        _row(1, updated_at=now),
        _row(2, updated_at=now),
        _row(3, updated_at=now),
        _row(4, updated_at=now + 365.0 * DAY),
    ]

    weighted = weighting.weight_optimizer_observations(observations)
    diagnostics = weighting.optimizer_observation_weight_diagnostics(weighted)

    assert [row["routing_age_seconds"] for row in weighted] == pytest.approx(
        [0.0, 0.0, 0.0, 0.0]
    )
    assert [row["routing_recency_weight"] for row in weighted] == pytest.approx(
        [1.0, 1.0, 1.0, 1.0]
    )
    assert diagnostics["effective_rows"] == pytest.approx(4.0)
    assert diagnostics["max_weight_share"] == pytest.approx(0.25)
    assert diagnostics["enter_confident"] is True
