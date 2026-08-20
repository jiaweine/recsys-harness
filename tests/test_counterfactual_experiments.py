from __future__ import annotations

import pytest

from lingjing_harness.counterfactual import CounterfactualRecord, evaluate_off_policy
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
    logging_policy: str = "prod-control",
    target_policy: str = "candidate-a",
) -> CounterfactualRecord:
    return CounterfactualRecord(
        decision_id=decision_id,
        surface="recommend",
        action_id=f"action-{decision_id}",
        reward=reward,
        logging_propensity=logging,
        target_propensity=target,
        logging_policy_id=logging_policy,
        target_policy_id=target_policy,
        logged_reward_estimate=logged_estimate,
        target_reward_estimate=target_estimate,
    )


def test_on_policy_ips_and_snips_equal_logged_reward():
    rows = [
        _record("d1", 1.0, 0.5, 0.5),
        _record("d2", 0.0, 0.25, 0.25),
        _record("d3", -1.0, 0.8, 0.8),
    ]
    report = evaluate_off_policy(rows)
    assert report["estimators"]["logged_mean"]["value"] == 0.0
    assert report["estimators"]["ips"]["value"] == 0.0
    assert report["estimators"]["snips"]["value"] == 0.0
    assert report["diagnostics"]["effective_sample_ratio"] == 1.0
    assert report["diagnostics"]["clipped_share"] == 0.0


def test_ips_snips_and_dr_match_hand_computed_contextual_bandit_example():
    rows = [
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
    report = evaluate_off_policy(rows, importance_weight_cap=20)
    assert report["estimators"]["logged_mean"]["value"] == 0.5
    assert report["estimators"]["ips"]["value"] == 0.25
    assert report["estimators"]["snips"]["value"] == 0.2
    assert report["estimators"]["dr"]["value"] == 0.3
    assert report["estimators"]["dr"]["direct_model_coverage"] == 1.0


def test_dr_is_explicitly_unavailable_without_complete_reward_model():
    rows = [
        _record("d1", 1.0, 0.5, 0.8),
        _record("d2", 0.0, 0.5, 0.2),
    ]
    report = evaluate_off_policy(rows)
    assert report["estimators"]["ips"]["available"] is True
    assert report["estimators"]["snips"]["available"] is True
    assert report["estimators"]["dr"]["available"] is False
    assert report["estimators"]["dr"]["value"] is None
    assert report["confidence"]["dr"]["available"] is False


def test_weight_clipping_and_effective_sample_size_are_reported_not_hidden():
    rows = [
        _record("d1", 1.0, 0.01, 1.0),
        _record("d2", 0.0, 1.0, 0.1),
        _record("d3", 0.0, 1.0, 0.1),
        _record("d4", 0.0, 1.0, 0.1),
    ]
    report = evaluate_off_policy(rows, importance_weight_cap=2.0)
    diagnostics = report["diagnostics"]
    assert diagnostics["raw_weight_max"] == 100.0
    assert diagnostics["weight_max"] == 2.0
    assert diagnostics["clipped_samples"] == 1
    assert diagnostics["clipped_share"] == 0.25
    assert 0.0 < diagnostics["effective_sample_ratio"] < 1.0


def test_counterfactual_input_rejects_invalid_probabilities_and_ambiguous_dr_inputs():
    with pytest.raises(ValueError, match="logging_propensity"):
        _record("bad", 1.0, 0.0, 0.5)
    with pytest.raises(ValueError, match="target_propensity"):
        _record("bad", 1.0, 0.5, 1.2)
    with pytest.raises(ValueError, match="both logged_reward_estimate"):
        CounterfactualRecord(
            decision_id="bad-dr",
            surface="recommend",
            action_id="a",
            reward=1.0,
            logging_propensity=0.5,
            target_propensity=0.5,
            logging_policy_id="prod-control",
            target_policy_id="candidate-a",
            logged_reward_estimate=0.4,
        )


def test_counterfactual_input_requires_one_logged_action_per_decision_identity():
    rows = [
        _record("same", 1.0, 0.5, 0.5),
        _record("same", 0.0, 0.5, 0.5),
    ]
    with pytest.raises(ValueError, match="one logged action per decision_id"):
        evaluate_off_policy(rows)


def test_counterfactual_parser_refuses_to_infer_missing_target_probability():
    with pytest.raises(ValueError, match="target_propensity"):
        CounterfactualRecord.from_dict(
            {
                "decision_id": "d1",
                "surface": "search",
                "action_id": "sku-1",
                "reward": 1,
                "logging_propensity": 0.3,
                "logging_policy_id": "prod-search",
                "target_policy_id": "candidate-search",
                "rank": 1,
                "score": 0.99,
            }
        )


def _uplift_records() -> list[CounterfactualRecord]:
    rows: list[CounterfactualRecord] = []
    for index in range(8):
        good = index < 4
        rows.append(
            _record(
                f"u-{index}",
                1.0 if good else 0.0,
                0.5,
                1.0 if good else 0.1,
            )
        )
    return rows


def _spec(criteria: ExperimentCriteria, *, estimator: str = "snips") -> ExperimentSpec:
    return ExperimentSpec(
        experiment_id="exp-candidate-a",
        surface="recommend",
        hypothesis="candidate improves business reward on logged traffic",
        logging_policy_id="prod-control",
        candidate_policy_id="candidate-a",
        primary_estimator=estimator,
        criteria=criteria,
        importance_weight_cap=20.0,
    )


def test_experiment_contract_can_advance_only_to_controlled_online_test():
    criteria = ExperimentCriteria(
        minimum_samples=8,
        minimum_effective_sample_ratio=0.5,
        maximum_clipped_share=0.0,
        minimum_support_coverage=1.0,
        minimum_probability_positive=0.5,
        minimum_estimated_delta=0.1,
    )
    result = evaluate_counterfactual_experiment(_uplift_records(), _spec(criteria))
    decision = result["decision"]
    assert decision["eligible_for_online_test"] is True
    assert decision["automatic_activation"] is False
    assert decision["next_step"] == "controlled_online_experiment"
    assert result["counterfactual_evaluation"]["estimators"]["snips"]["delta_vs_logged"] > 0.1


def test_experiment_contract_blocks_thin_overlap_even_when_point_estimate_looks_good():
    criteria = ExperimentCriteria(
        minimum_samples=4,
        minimum_effective_sample_ratio=0.9,
        maximum_clipped_share=0.0,
        minimum_support_coverage=1.0,
        minimum_probability_positive=0.0,
        minimum_estimated_delta=-1.0,
    )
    rows = [
        _record("d1", 1.0, 0.01, 1.0),
        _record("d2", 0.0, 1.0, 0.01),
        _record("d3", 0.0, 1.0, 0.01),
        _record("d4", 0.0, 1.0, 0.01),
    ]
    result = evaluate_counterfactual_experiment(rows, _spec(criteria))
    decision = result["decision"]
    assert decision["eligible_for_online_test"] is False
    assert any(blocker.startswith("effective_sample_ratio<") for blocker in decision["blockers"])


def test_dr_primary_experiment_is_blocked_when_reward_model_is_missing():
    criteria = ExperimentCriteria(
        minimum_samples=2,
        minimum_effective_sample_ratio=0.0,
        maximum_clipped_share=1.0,
        minimum_support_coverage=0.0,
        minimum_probability_positive=0.0,
        minimum_estimated_delta=-1.0,
    )
    result = evaluate_counterfactual_experiment(
        [_record("d1", 1.0, 0.5, 0.5), _record("d2", 0.0, 0.5, 0.5)],
        _spec(criteria, estimator="dr"),
    )
    assert result["decision"]["eligible_for_online_test"] is False
    assert "dr_unavailable" in result["decision"]["blockers"]


def test_experiment_rejects_policy_or_surface_mixing():
    criteria = ExperimentCriteria(
        minimum_samples=1,
        minimum_effective_sample_ratio=0.0,
        maximum_clipped_share=1.0,
        minimum_support_coverage=0.0,
        minimum_probability_positive=0.0,
    )
    spec = _spec(criteria)
    wrong_policy = _record(
        "d1",
        1.0,
        0.5,
        0.5,
        target_policy="candidate-b",
    )
    with pytest.raises(ValueError, match="target_policy_id"):
        evaluate_counterfactual_experiment([wrong_policy], spec)
