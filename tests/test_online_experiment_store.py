from __future__ import annotations

from pathlib import Path

import pytest

from lingjing_harness.online_experiment_store import (
    DurableOnlineExperimentStore,
    ExperimentConflict,
    ExperimentStateError,
)
from lingjing_harness.online_experiments import (
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
)


def _spec(experiment_id: str = "exp-1", *, final_only: bool = False) -> OnlineExperimentSpec:
    stages = (
        (RampStage(0, 0.5, 200),)
        if final_only
        else (RampStage(0, 0.25, 200), RampStage(1, 0.5, 300))
    )
    return OnlineExperimentSpec(
        experiment_id=experiment_id,
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
                minimum_samples_per_arm=50,
                cuped_covariate="pre_conversion",
            ),
            OnlineMetricSpec(
                name="error",
                role="guardrail",
                kind="bernoulli",
                direction="lower_is_better",
                advance_threshold=-0.03,
                rollback_threshold=-0.08,
                minimum_samples_per_arm=50,
            ),
        ),
        stages=stages,
        outcome_alpha=0.05,
        srm_alpha=0.01,
    )


def _binary(index: int, rate: float, *, period: int = 100) -> float:
    return 1.0 if index % period < round(rate * period) else 0.0


def _epoch_rows(
    *,
    epoch_id: str,
    start_sequence: int,
    control_count: int,
    candidate_count: int,
    control_conversion: float,
    candidate_conversion: float,
    control_error: float,
    candidate_error: float,
) -> list[OnlineObservation]:
    total = control_count + candidate_count
    rows: list[OnlineObservation] = []
    used_control = 0
    used_candidate = 0
    for offset in range(total):
        desired_candidate = round((offset + 1) * candidate_count / total)
        candidate = desired_candidate > used_candidate
        if candidate:
            index = used_candidate
            used_candidate += 1
            arm = "candidate"
            unit_id = f"{epoch_id}-t-{index}"
            conversion = candidate_conversion
            error = candidate_error
        else:
            index = used_control
            used_control += 1
            arm = "control"
            unit_id = f"{epoch_id}-c-{index}"
            conversion = control_conversion
            error = control_error
        rows.append(
            OnlineObservation(
                unit_id=unit_id,
                sequence=start_sequence + offset,
                epoch_id=epoch_id,
                arm=arm,
                metrics={
                    "conversion": _binary(index, conversion),
                    "error": _binary(index, error),
                },
                pre_exposure={"pre_conversion": (index % 10) / 10.0},
            )
        )
    assert used_control == control_count
    assert used_candidate == candidate_count
    return rows


def _store(tmp_path: Path) -> DurableOnlineExperimentStore:
    return DurableOnlineExperimentStore(tmp_path / "workspace.db")


def test_registry_persists_across_store_instances_and_create_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    created = store.create_experiment(_spec(), initial_epoch_id="e0")

    reopened = _store(tmp_path)
    same = reopened.create_experiment(_spec(), initial_epoch_id="e0")

    assert created["version"] == 1
    assert same["version"] == 1
    assert same["current_epoch_id"] == "e0"
    assert same["current_candidate_fraction"] == pytest.approx(0.25)
    assert same["automatic_activation"] is False
    assert reopened.events("exp-1")[0]["event_type"] == "experiment_created"


def test_create_same_id_with_different_contract_fails_closed(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")

    changed = _spec("exp-1", final_only=True)
    with pytest.raises(ExperimentConflict, match="different contract"):
        store.create_experiment(changed, initial_epoch_id="e0")


def test_delayed_metrics_are_monotonic_and_conflicts_rollback_batch(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="e0")
    assignment = OnlineObservation(
        unit_id="u-1",
        sequence=0,
        epoch_id="e0",
        arm="control",
        metrics={},
        pre_exposure={"pre_conversion": 0.3},
    )
    first = store.ingest_observations("exp-1", [assignment])
    assert first["version"] == 2
    assert first["inserted_units"] == 1

    matured = OnlineObservation(
        unit_id="u-1",
        sequence=0,
        epoch_id="e0",
        arm="control",
        metrics={"conversion": 1.0, "error": 0.0},
        pre_exposure={"pre_conversion": 0.3},
    )
    second = store.ingest_observations("exp-1", [matured])
    assert second["version"] == 3
    assert second["matured_units"] == 1

    duplicate = store.ingest_observations("exp-1", [matured])
    assert duplicate["version"] == 3
    assert duplicate["idempotent_units"] == 1
    assert duplicate["evidence_changed"] is False

    conflicting_existing = OnlineObservation(
        unit_id="u-1",
        sequence=0,
        epoch_id="e0",
        arm="control",
        metrics={"conversion": 0.0},
    )
    new_unit = OnlineObservation(
        unit_id="u-2",
        sequence=1,
        epoch_id="e0",
        arm="candidate",
        metrics={"conversion": 1.0},
    )
    with pytest.raises(ExperimentConflict, match="conflicting metric"):
        store.ingest_observations("exp-1", [new_unit, conflicting_existing])

    record = store.get_experiment("exp-1")
    assert record["version"] == 3
    assert record["observation_count"] == 1


def test_assignment_identity_and_sequence_are_immutable(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="e0")
    original = OnlineObservation("u-1", 0, "e0", "control", {"conversion": 0.0})
    store.ingest_observations("exp-1", [original])

    with pytest.raises(ExperimentConflict, match="identity is immutable"):
        store.ingest_observations(
            "exp-1",
            [OnlineObservation("u-1", 0, "e0", "candidate", {"conversion": 0.0})],
        )
    with pytest.raises(ExperimentConflict, match="sequence already owned"):
        store.ingest_observations(
            "exp-1",
            [OnlineObservation("u-2", 0, "e0", "candidate", {"conversion": 1.0})],
        )


def test_batch_changes_increment_evidence_version_once(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="e0")
    rows = [
        OnlineObservation(f"u-{index}", index, "e0", "control" if index % 2 == 0 else "candidate")
        for index in range(20)
    ]
    result = store.ingest_observations("exp-1", rows)

    assert result["inserted_units"] == 20
    assert result["version"] == 2
    assert store.get_experiment("exp-1")["observation_count"] == 20


def test_fresh_evidence_can_advance_ramp_but_stale_version_cannot(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=300,
        candidate_count=100,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.30,
        candidate_error=0.01,
    )
    ingest = store.ingest_observations("exp-1", rows)
    evaluation = store.evaluate("exp-1")

    assert evaluation["decision"]["action"] == "advance_ramp"
    assert evaluation["registry"]["version"] == ingest["version"]
    applied = store.apply_recommendation(
        "exp-1",
        expected_version=ingest["version"],
        action="advance_ramp",
        new_epoch_id="e1",
    )
    assert applied["experiment"]["current_stage_index"] == 1
    assert applied["experiment"]["current_candidate_fraction"] == pytest.approx(0.5)
    assert applied["traffic_directive"]["automatic_apply"] is False
    assert applied["traffic_directive"]["production_activation"] is False

    with pytest.raises(ExperimentConflict, match="stale experiment version") as exc_info:
        store.apply_recommendation(
            "exp-1",
            expected_version=ingest["version"],
            action="advance_ramp",
            new_epoch_id="e2",
        )
    assert exc_info.value.current_version == ingest["version"] + 1


def test_new_evidence_invalidates_previously_read_transition_version(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=300,
        candidate_count=100,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.30,
        candidate_error=0.01,
    )
    store.ingest_observations("exp-1", rows)
    evaluation = store.evaluate("exp-1")
    stale_version = evaluation["registry"]["version"]

    last_sequence = max(row.sequence for row in rows)
    extra = OnlineObservation(
        unit_id="e0-c-extra",
        sequence=last_sequence + 1,
        epoch_id="e0",
        arm="control",
        metrics={"conversion": 0.0, "error": 0.0},
    )
    store.ingest_observations("exp-1", [extra])

    with pytest.raises(ExperimentConflict, match="stale experiment version"):
        store.apply_recommendation(
            "exp-1",
            expected_version=stale_version,
            action="advance_ramp",
            new_epoch_id="e1",
        )


def test_new_assignments_are_fenced_to_current_allocation_epoch(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(), initial_epoch_id="e0")
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=1000,
        control_count=300,
        candidate_count=100,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.30,
        candidate_error=0.01,
    )
    # Leave one pre-exposure field delayed so we can prove historical epochs still
    # accept maturation for an already-randomized unit after the ramp advances.
    first = rows[0]
    rows[0] = OnlineObservation(
        first.unit_id,
        first.sequence,
        first.epoch_id,
        first.arm,
        first.metrics,
        {},
    )
    ingest = store.ingest_observations("exp-1", rows)
    assert store.evaluate("exp-1")["decision"]["action"] == "advance_ramp"
    advanced = store.apply_recommendation(
        "exp-1",
        expected_version=ingest["version"],
        action="advance_ramp",
        new_epoch_id="e1",
    )
    assert advanced["experiment"]["current_epoch_id"] == "e1"

    with pytest.raises(ExperimentConflict, match="current allocation epoch"):
        store.ingest_observations(
            "exp-1",
            [OnlineObservation("late-e0-new", 1400, "e0", "control")],
        )

    matured = store.ingest_observations(
        "exp-1",
        [
            OnlineObservation(
                first.unit_id,
                first.sequence,
                "e0",
                first.arm,
                first.metrics,
                {"pre_conversion": 0.7},
            )
        ],
    )
    assert matured["matured_units"] == 1
    assert matured["version"] == advanced["experiment"]["version"] + 1

    with pytest.raises(ExperimentConflict, match="existing assignment history"):
        store.ingest_observations(
            "exp-1",
            [OnlineObservation("bad-e1-order", 500, "e1", "candidate")],
        )

    valid = store.ingest_observations(
        "exp-1",
        [OnlineObservation("good-e1", 1600, "e1", "candidate")],
    )
    assert valid["inserted_units"] == 1

    # Current-epoch batches may arrive out of sequence order across workers. The
    # evidence version changes, so any earlier recommendation becomes stale and the
    # transition CAS recomputes the complete sequence-ordered evidence before commit.
    late_same_epoch = store.ingest_observations(
        "exp-1",
        [OnlineObservation("late-e1-backfill", 1500, "e1", "control")],
    )
    assert late_same_epoch["inserted_units"] == 1


def test_harmful_guardrail_marks_rollback_without_applying_traffic(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="e0")
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=200,
        candidate_count=200,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.01,
        candidate_error=0.95,
    )
    ingest = store.ingest_observations("exp-1", rows)
    evaluation = store.evaluate("exp-1")
    assert evaluation["decision"]["action"] == "rollback_recommended"

    applied = store.apply_recommendation(
        "exp-1",
        expected_version=ingest["version"],
        action="rollback_recommended",
    )
    directive = applied["traffic_directive"]
    assert applied["experiment"]["status"] == "rollback_required"
    assert directive["recommended_candidate_fraction"] == 0.0
    assert directive["automatic_apply"] is False
    assert directive["production_activation"] is False

    with pytest.raises(ExperimentStateError, match="not transitionable"):
        store.apply_recommendation(
            "exp-1",
            expected_version=applied["experiment"]["version"],
            action="rollback_recommended",
        )


def test_final_stage_success_only_marks_promotion_review(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="final")
    rows = _epoch_rows(
        epoch_id="final",
        start_sequence=0,
        control_count=200,
        candidate_count=200,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.30,
        candidate_error=0.01,
    )
    ingest = store.ingest_observations("exp-1", rows)
    evaluation = store.evaluate("exp-1")
    assert evaluation["decision"]["action"] == "eligible_for_promotion_review"

    applied = store.apply_recommendation(
        "exp-1",
        expected_version=ingest["version"],
        action="eligible_for_promotion_review",
    )
    directive = applied["traffic_directive"]
    assert applied["experiment"]["status"] == "promotion_review"
    assert directive["recommended_candidate_fraction"] == pytest.approx(0.5)
    assert directive["recommendation"] == "hold_controlled_allocation_pending_promotion_review"
    assert directive["production_activation"] is False
    assert applied["automatic_activation"] is False


def test_audit_events_follow_evidence_and_transition_versions(tmp_path: Path):
    store = _store(tmp_path)
    store.create_experiment(_spec(final_only=True), initial_epoch_id="e0")
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=200,
        candidate_count=200,
        control_conversion=0.10,
        candidate_conversion=0.90,
        control_error=0.30,
        candidate_error=0.01,
    )
    ingest = store.ingest_observations("exp-1", rows)
    store.apply_recommendation(
        "exp-1",
        expected_version=ingest["version"],
        action="eligible_for_promotion_review",
    )
    events = store.events("exp-1")

    assert [row["event_type"] for row in events] == [
        "experiment_created",
        "observations_ingested",
        "promotion_review_marked",
    ]
    assert [row["version"] for row in events] == [1, 2, 3]
    assert events[-1]["payload"]["automatic_activation"] is False
