from __future__ import annotations

import pytest

from lingjing_harness.online_experiments import (
    AllocationEpoch,
    OnlineExperimentSpec,
    OnlineMetricSpec,
    OnlineObservation,
    RampStage,
    bernoulli_mixture_confidence_sequence,
    bounded_alpha_spending_confidence_sequence,
    evaluate_online_experiment,
)


def _spec(*, final_only: bool = False) -> OnlineExperimentSpec:
    stages = (
        (RampStage(0, 0.5, 200),)
        if final_only
        else (
            RampStage(0, 0.25, 200),
            RampStage(1, 0.5, 300),
        )
    )
    return OnlineExperimentSpec(
        experiment_id="online-1",
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
                minimum_samples_per_arm=80,
                cuped_covariate="pre_conversion",
            ),
            OnlineMetricSpec(
                name="error",
                role="guardrail",
                kind="bernoulli",
                direction="lower_is_better",
                advance_threshold=-0.03,
                rollback_threshold=-0.08,
                minimum_samples_per_arm=80,
            ),
        ),
        stages=stages,
        outcome_alpha=0.05,
        srm_alpha=0.01,
    )


def _bernoulli_pattern(index: int, rate: float, period: int = 100) -> float:
    return 1.0 if (index % period) < round(rate * period) else 0.0


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
    """Build assignment-order rows with arms spread close to their final ratio."""

    total = control_count + candidate_count
    rows: list[OnlineObservation] = []
    used_control = 0
    used_candidate = 0
    for offset in range(total):
        desired_candidate = round((offset + 1) * candidate_count / total)
        choose_candidate = desired_candidate > used_candidate
        if choose_candidate:
            index = used_candidate
            used_candidate += 1
            arm = "candidate"
            conversion = candidate_conversion
            error = candidate_error
            unit_id = f"{epoch_id}-t-{index}"
        else:
            index = used_control
            used_control += 1
            arm = "control"
            conversion = control_conversion
            error = control_error
            unit_id = f"{epoch_id}-c-{index}"
        rows.append(
            OnlineObservation(
                unit_id=unit_id,
                sequence=start_sequence + offset,
                epoch_id=epoch_id,
                arm=arm,
                metrics={
                    "conversion": _bernoulli_pattern(index, conversion),
                    "error": _bernoulli_pattern(index, error),
                },
                pre_exposure={"pre_conversion": (index % 10) / 10.0},
            )
        )
    assert used_control == control_count
    assert used_candidate == candidate_count
    return rows


def test_bernoulli_mixture_cs_contains_empirical_center_and_shrinks():
    short = bernoulli_mixture_confidence_sequence(
        [1.0, 0.0] * 20,
        alpha=0.025,
    )
    long = bernoulli_mixture_confidence_sequence(
        [1.0, 0.0] * 400,
        alpha=0.025,
    )

    assert short.lower < 0.5 < short.upper
    assert long.lower < 0.5 < long.upper
    assert (long.upper - long.lower) < (short.upper - short.lower)


def test_bounded_alpha_spending_cs_is_time_uniform_and_shrinks_with_more_data():
    short = bounded_alpha_spending_confidence_sequence([0.5] * 20, alpha=0.025)
    long = bounded_alpha_spending_confidence_sequence([0.5] * 1000, alpha=0.025)

    assert short.lower <= 0.5 <= short.upper
    assert long.lower <= 0.5 <= long.upper
    assert (long.upper - long.lower) < (short.upper - short.lower)


def test_epoch_specific_srm_accepts_correct_25_then_50_percent_allocations():
    spec = _spec()
    epochs = (
        AllocationEpoch("e0", 0, 0.25),
        AllocationEpoch("e1", 1, 0.5),
    )
    rows0 = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=300,
        candidate_count=100,
        control_conversion=0.2,
        candidate_conversion=0.8,
        control_error=0.2,
        candidate_error=0.05,
    )
    rows1 = _epoch_rows(
        epoch_id="e1",
        start_sequence=400,
        control_count=200,
        candidate_count=200,
        control_conversion=0.2,
        candidate_conversion=0.8,
        control_error=0.2,
        candidate_error=0.05,
    )
    result = evaluate_online_experiment(
        [*rows0, *rows1],
        spec,
        epochs=epochs,
        current_epoch_id="e1",
    )

    assert result["srm"]["failed_anytime"] is False
    assert [row["expected_candidate_fraction"] for row in result["srm"]["per_epoch"]] == [
        0.25,
        0.5,
    ]


def test_srm_anytime_alarm_remains_failed_after_later_counts_rebalance():
    spec = OnlineExperimentSpec(
        experiment_id="srm-lock",
        control_arm="control",
        candidate_arm="candidate",
        metrics=(
            OnlineMetricSpec(
                name="conversion",
                role="primary",
                kind="bernoulli",
                direction="higher_is_better",
                advance_threshold=0.0,
                rollback_threshold=-0.1,
                minimum_samples_per_arm=1,
            ),
        ),
        stages=(RampStage(0, 0.5, 2),),
        srm_alpha=0.01,
    )
    epoch = (AllocationEpoch("e0", 0, 0.5),)
    rows = []
    for index in range(25):
        rows.append(
            OnlineObservation(
                unit_id=f"t-{index}",
                sequence=index,
                epoch_id="e0",
                arm="candidate",
                metrics={"conversion": 1.0},
            )
        )
    for index in range(25):
        rows.append(
            OnlineObservation(
                unit_id=f"c-{index}",
                sequence=25 + index,
                epoch_id="e0",
                arm="control",
                metrics={"conversion": 0.0},
            )
        )

    result = evaluate_online_experiment(
        rows,
        spec,
        epochs=epoch,
        current_epoch_id="e0",
    )

    assert result["srm"]["failed_anytime"] is True
    assert result["srm"]["first_crossing_sequence"] is not None
    assert result["decision"]["action"] == "rollback_recommended"
    assert "sample_ratio_mismatch" in result["decision"]["harmful_signals"]


def test_clear_primary_win_and_safe_guardrail_advances_ramp_without_activation():
    spec = _spec()
    epochs = (AllocationEpoch("e0", 0, 0.25),)
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=300,
        candidate_count=100,
        control_conversion=0.15,
        candidate_conversion=0.85,
        control_error=0.30,
        candidate_error=0.02,
    )

    result = evaluate_online_experiment(
        rows,
        spec,
        epochs=epochs,
        current_epoch_id="e0",
    )

    assert result["decision"]["action"] == "advance_ramp"
    assert result["decision"]["next_stage_index"] == 1
    assert result["decision"]["automatic_activation"] is False
    assert set(result["decision"]["passed_metrics"]) == {"conversion", "error"}
    assert result["metrics"]["conversion"]["cuped"]["decision_uses_cuped"] is False


def test_confidently_harmful_guardrail_auto_kills_candidate_ramp():
    spec = _spec()
    epochs = (AllocationEpoch("e0", 0, 0.25),)
    rows = _epoch_rows(
        epoch_id="e0",
        start_sequence=0,
        control_count=600,
        candidate_count=200,
        control_conversion=0.20,
        candidate_conversion=0.80,
        control_error=0.05,
        candidate_error=0.90,
    )

    result = evaluate_online_experiment(
        rows,
        spec,
        epochs=epochs,
        current_epoch_id="e0",
    )

    assert result["metrics"]["error"]["status"] == "confidently_harmful"
    assert result["decision"]["action"] == "rollback_recommended"
    assert "error" in result["decision"]["harmful_signals"]
    assert result["decision"]["automatic_activation"] is False


def test_outcome_inference_uses_only_current_ramp_epoch():
    spec = _spec()
    epochs = (
        AllocationEpoch("bad-old", 0, 0.25),
        AllocationEpoch("good-current", 1, 0.5),
    )
    old_rows = _epoch_rows(
        epoch_id="bad-old",
        start_sequence=0,
        control_count=300,
        candidate_count=100,
        control_conversion=0.9,
        candidate_conversion=0.1,
        control_error=0.02,
        candidate_error=0.8,
    )
    current_rows = _epoch_rows(
        epoch_id="good-current",
        start_sequence=400,
        control_count=250,
        candidate_count=250,
        control_conversion=0.1,
        candidate_conversion=0.9,
        control_error=0.3,
        candidate_error=0.01,
    )

    result = evaluate_online_experiment(
        [*old_rows, *current_rows],
        spec,
        epochs=epochs,
        current_epoch_id="good-current",
    )

    assert result["metrics"]["conversion"]["current_epoch_only"] is True
    assert result["metrics"]["conversion"]["control_mature_samples"] == 250
    assert result["metrics"]["conversion"]["candidate_mature_samples"] == 250
    assert result["decision"]["action"] == "eligible_for_promotion_review"
    assert result["decision"]["automatic_activation"] is False


def test_final_stage_success_only_becomes_eligible_for_promotion_review():
    spec = _spec(final_only=True)
    epochs = (AllocationEpoch("final", 0, 0.5),)
    rows = _epoch_rows(
        epoch_id="final",
        start_sequence=0,
        control_count=200,
        candidate_count=200,
        control_conversion=0.1,
        candidate_conversion=0.9,
        control_error=0.3,
        candidate_error=0.01,
    )

    result = evaluate_online_experiment(
        rows,
        spec,
        epochs=epochs,
        current_epoch_id="final",
    )

    assert result["decision"]["action"] == "eligible_for_promotion_review"
    assert result["decision"]["next_stage_index"] is None
    assert result["decision"]["automatic_activation"] is False
    assert result["decision"]["activation_authority"] == "external_promotion_review_only"


def test_missing_delayed_metrics_holds_until_each_arm_is_mature():
    spec = _spec(final_only=True)
    epochs = (AllocationEpoch("e0", 0, 0.5),)
    rows = []
    for index in range(100):
        rows.append(
            OnlineObservation(
                unit_id=f"c-{index}",
                sequence=index * 2,
                epoch_id="e0",
                arm="control",
                metrics={"conversion": 0.0, "error": 0.0},
            )
        )
        rows.append(
            OnlineObservation(
                unit_id=f"t-{index}",
                sequence=index * 2 + 1,
                epoch_id="e0",
                arm="candidate",
                metrics=(
                    {"conversion": 1.0, "error": 0.0}
                    if index < 20
                    else {}
                ),
            )
        )

    result = evaluate_online_experiment(
        rows,
        spec,
        epochs=epochs,
        current_epoch_id="e0",
    )

    assert result["decision"]["action"] == "hold"
    assert result["metrics"]["conversion"]["status"] == "insufficient_maturity"
    assert "conversion:insufficient_maturity" in result["decision"]["blockers"]


def test_duplicate_randomization_units_are_rejected():
    spec = _spec(final_only=True)
    epochs = (AllocationEpoch("e0", 0, 0.5),)
    rows = [
        OnlineObservation("same", 0, "e0", "control", {"conversion": 0.0}),
        OnlineObservation("same", 1, "e0", "candidate", {"conversion": 1.0}),
    ]

    with pytest.raises(ValueError, match="duplicate randomized unit_id"):
        evaluate_online_experiment(rows, spec, epochs=epochs, current_epoch_id="e0")
