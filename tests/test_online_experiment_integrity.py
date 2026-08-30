from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from lingjing_harness.online_experiment_api import (
    ObservationRequest,
    RecommendationApplyRequest,
)
from lingjing_harness.online_experiment_store import DurableOnlineExperimentStore
from lingjing_harness.online_experiments import (
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
)


def _spec() -> OnlineExperimentSpec:
    return OnlineExperimentSpec(
        experiment_id="integrity-exp",
        control_arm="control",
        candidate_arm="candidate",
        metrics=(
            OnlineMetricSpec(
                name="conversion",
                role="primary",
                kind="bernoulli",
                direction="higher_is_better",
                advance_threshold=0.05,
                rollback_threshold=-0.05,
                minimum_samples_per_arm=2,
            ),
        ),
        stages=(RampStage(0, 0.25, 2), RampStage(1, 0.5, 4)),
    )


def _store(tmp_path: Path) -> DurableOnlineExperimentStore:
    return DurableOnlineExperimentStore(tmp_path / "workspace.db")


def test_create_retry_remains_idempotent_after_ramp_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")

    monkeypatch.setattr(
        store,
        "_evaluate_from_connection",
        lambda connection, experiment_id: {
            "decision": {"action": "advance_ramp", "next_stage_index": 1},
            "srm": {
                "failed_anytime": False,
                "max_e_value": 1.0,
                "first_crossing_sequence": None,
            },
            "metrics": {},
        },
    )
    advanced = store.apply_recommendation(
        "integrity-exp",
        expected_version=1,
        action="advance_ramp",
        new_epoch_id="e1",
    )
    assert advanced["experiment"]["current_epoch_id"] == "e1"
    assert advanced["experiment"]["version"] == 2

    retried = store.create_experiment(_spec(), initial_epoch_id="e0")
    assert retried["current_epoch_id"] == "e1"
    assert retried["version"] == 2
    assert [row["event_type"] for row in store.events("integrity-exp")] == [
        "experiment_created",
        "ramp_advanced",
    ]


def test_store_rejects_coerced_assignment_and_metric_values(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")

    with pytest.raises(ValueError, match="integer, not boolean"):
        store.ingest_observations(
            "integrity-exp",
            [OnlineObservation("u-bool-sequence", True, "e0", "control")],
        )
    with pytest.raises(ValueError, match="must be an integer"):
        store.ingest_observations(
            "integrity-exp",
            [OnlineObservation("u-float-sequence", 1.0, "e0", "control")],
        )
    with pytest.raises(ValueError, match="numeric, not boolean"):
        store.ingest_observations(
            "integrity-exp",
            [
                OnlineObservation(
                    "u-bool-metric",
                    0,
                    "e0",
                    "control",
                    {"conversion": True},
                )
            ],
        )

    assert store.get_experiment("integrity-exp")["observation_count"] == 0
    assert store.get_experiment("integrity-exp")["version"] == 1


def test_store_rejects_coerced_cas_versions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")

    with pytest.raises(ValueError, match="integer, not boolean"):
        store.apply_recommendation(
            "integrity-exp",
            expected_version=True,
            action="advance_ramp",
            new_epoch_id="e1",
        )
    with pytest.raises(ValueError, match="must be an integer"):
        store.apply_recommendation(
            "integrity-exp",
            expected_version=1.0,
            action="advance_ramp",
            new_epoch_id="e1",
        )

    assert store.get_experiment("integrity-exp")["version"] == 1


def test_api_models_reject_implicit_numeric_coercion() -> None:
    base = {
        "unit_id": "u-1",
        "sequence": 1,
        "epoch_id": "e0",
        "arm": "control",
        "metrics": {"conversion": 1.0},
    }
    for mutation in (
        {"sequence": True},
        {"sequence": 1.0},
        {"sequence": "1"},
        {"metrics": {"conversion": True}},
        {"metrics": {"conversion": "1.0"}},
    ):
        with pytest.raises(ValidationError):
            ObservationRequest.model_validate({**base, **mutation})

    for version in (True, 1.0, "1"):
        with pytest.raises(ValidationError):
            RecommendationApplyRequest.model_validate(
                {"expected_version": version, "action": "advance_ramp", "new_epoch_id": "e1"}
            )
