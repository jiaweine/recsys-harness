from __future__ import annotations

import math

from lingjing_harness.counterfactual import CounterfactualRecord
from lingjing_harness.counterfactual_robust import (
    direct_method,
    dros_doubly_robust,
    evaluate_robust_off_policy,
    raw_doubly_robust,
    switch_doubly_robust,
)
from lingjing_harness.experiments import (
    ExperimentCriteria,
    ExperimentSpec,
    evaluate_counterfactual_experiment,
)


def _record(
    decision_id: str,
    reward: float,
    logging: float,
    target: float,
    *,
    logged_estimate: float | None = None,
    target_estimate: float | None = None,
) -> CounterfactualRecord:
    return CounterfactualRecord(
        decision_id=decision_id,
        surface="recommend",
        action_id=f"action-{decision_id}",
        reward=reward,
        logging_propensity=logging,
        target_propensity=target,
        logging_policy_id="prod-control",
        target_policy_id="candidate-a",
        logged_reward_estimate=logged_estimate,
        target_reward_estimate=target_estimate,
    )


def _complete_rows() -> list[CounterfactualRecord]:
    return [
        _record(
            "d1",
            1.0,
            0.5,
            0.25,
            logged_estimate=0.8,
            target_estimate=0.4,
        ),
        _record(
            "d2",
            0.0,
            0.25,
            0.5,
            logged_estimate=0.1,
            target_estimate=0.3,
        ),
    ]


def _heavy_tail_rows() -> list[CounterfactualRecord]:
    return [
        _record(
            "h1",
            1.0,
            0.01,
            1.0,
            logged_estimate=0.2,
            target_estimate=0.2,
        ),
        _record(
            "h2",
            0.0,
            1.0,
            0.1,
            logged_estimate=0.2,
            target_estimate=0.2,
        ),
        _record(
            "h3",
            0.0,
            1.0,
            0.1,
            logged_estimate=0.2,
            target_estimate=0.2,
        ),
        _record(
            "h4",
            0.0,
            1.0,
            0.1,
            logged_estimate=0.2,
            target_estimate=0.2,
        ),
    ]


def test_robust_estimators_match_hand_computed_example() -> None:
    rows = _complete_rows()

    assert direct_method(rows) == 0.35
    assert raw_doubly_robust(rows) == 0.3
    assert switch_doubly_robust(rows, tau=1.0) == 0.4
    assert math.isclose(dros_doubly_robust(rows, lambda_=1.0), 0.37)


def test_robust_bootstrap_allows_resampled_duplicate_decisions_and_retunes() -> None:
    rows = [
        _record(
            f"r{index}",
            1.0 if index % 3 == 0 else 0.0,
            0.25 + 0.05 * (index % 4),
            0.15 + 0.08 * (index % 5),
            logged_estimate=0.25,
            target_estimate=0.30,
        )
        for index in range(12)
    ]

    first = evaluate_robust_off_policy(rows, bootstrap_iterations=180)
    second = evaluate_robust_off_policy(rows, bootstrap_iterations=180)

    assert first == second
    assert first["available"] is True
    for estimator in ("raw_dr", "switch_dr", "dros"):
        confidence = first["confidence"][estimator]
        assert confidence["available"] is True
        assert confidence["ci95"] is not None
        assert confidence["bootstrap_baseline"] == "resampled_logged_mean"
    assert (
        first["confidence"]["switch_dr"]["tuning_semantics"]
        == "retuned_within_each_bootstrap_resample"
    )
    assert (
        first["confidence"]["dros"]["tuning_semantics"]
        == "retuned_within_each_bootstrap_resample"
    )


def test_heavy_tail_diagnostics_expose_raw_dr_instability_and_robust_shrinkage() -> None:
    report = evaluate_robust_off_policy(_heavy_tail_rows(), bootstrap_iterations=160)
    diagnostics = report["diagnostics"]
    estimators = report["estimators"]

    assert diagnostics["heavy_tail_detected"] is True
    assert diagnostics["raw_weight_max"] == 100.0
    assert diagnostics["raw_effective_sample_ratio"] < 0.3
    assert diagnostics["recommended_estimator"] in {"switch_dr", "dros"}
    assert estimators["raw_dr"]["value"] > 10.0
    assert abs(estimators["switch_dr"]["value"]) < abs(estimators["raw_dr"]["value"])
    assert abs(estimators["dros"]["value"]) < abs(estimators["raw_dr"]["value"])
    assert diagnostics["robust_estimator_spread"] > 1.0


def test_robust_ope_is_unavailable_without_complete_reward_model() -> None:
    rows = [
        _record("m1", 1.0, 0.5, 0.5),
        _record(
            "m2",
            0.0,
            0.5,
            0.5,
            logged_estimate=0.2,
            target_estimate=0.3,
        ),
    ]
    report = evaluate_robust_off_policy(rows)

    assert report["available"] is False
    assert report["diagnostics"]["reward_model_coverage"] == 0.5
    assert "complete reward-model" in report["reason"]


def _criteria(*, maximum_estimator_spread: float | None) -> ExperimentCriteria:
    return ExperimentCriteria(
        minimum_samples=2,
        minimum_effective_sample_ratio=0.0,
        maximum_clipped_share=1.0,
        minimum_support_coverage=0.0,
        minimum_probability_positive=0.0,
        minimum_estimated_delta=-100.0,
        maximum_estimator_spread=maximum_estimator_spread,
    )


def _spec(criteria: ExperimentCriteria, estimator: str) -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id=f"exp-{estimator}",
        surface="recommend",
        hypothesis="candidate improves reward",
        logging_policy_id="prod-control",
        candidate_policy_id="candidate-a",
        primary_estimator=estimator,
        criteria=criteria,
        importance_weight_cap=20.0,
    )


def test_experiment_can_use_explicit_robust_primary_without_granting_activation() -> None:
    rows = [
        _record(
            f"e{index}",
            1.0 if index < 4 else 0.0,
            0.5,
            0.8 if index < 4 else 0.2,
            logged_estimate=0.35,
            target_estimate=0.45,
        )
        for index in range(8)
    ]
    result = evaluate_counterfactual_experiment(
        rows,
        _spec(_criteria(maximum_estimator_spread=10.0), "switch_dr"),
        bootstrap_iterations=180,
    )

    assert result["decision"]["primary_estimator"] == "switch_dr"
    assert result["decision"]["primary_estimator_family"] == "robust_doubly_robust"
    assert result["decision"]["eligible_for_online_test"] is True
    assert result["decision"]["automatic_activation"] is False
    assert result["robust_counterfactual_evaluation"]["available"] is True


def test_estimator_disagreement_can_block_online_experiment() -> None:
    result = evaluate_counterfactual_experiment(
        _heavy_tail_rows(),
        _spec(_criteria(maximum_estimator_spread=0.01), "dros"),
        bootstrap_iterations=160,
    )

    assert result["decision"]["eligible_for_online_test"] is False
    assert any(
        blocker.startswith("estimator_spread>")
        for blocker in result["decision"]["blockers"]
    )
    assert result["decision"]["automatic_activation"] is False
