from __future__ import annotations

import pytest

from lingjing_harness.slate_counterfactual import SlatePositionRecord
from lingjing_harness.slate_experiments import (
    SlateExperimentCriteria,
    SlateExperimentSpec,
    evaluate_slate_experiment,
)


def _row(
    slate_id: str,
    position: int,
    action_id: str,
    reward: float,
    *,
    item_logging: float,
    item_target: float,
    cascade_logging: float,
    cascade_target: float,
    q_values: dict[str, float] | None = None,
    target_dist: dict[str, float] | None = None,
) -> SlatePositionRecord:
    return SlatePositionRecord(
        slate_id=slate_id,
        surface="recommend",
        position=position,
        action_id=action_id,
        reward=reward,
        logging_policy_id="prod-control",
        target_policy_id="candidate-a",
        logging_item_position_propensity=item_logging,
        target_item_position_propensity=item_target,
        logging_cascade_propensity=cascade_logging,
        target_cascade_propensity=cascade_target,
        q_values=q_values,
        target_action_distribution=target_dist,
    )


def _criteria(**overrides) -> SlateExperimentCriteria:
    values = {
        "minimum_slates": 2,
        "minimum_effective_sample_ratio": 0.5,
        "maximum_importance_weight": 5.0,
        "minimum_probability_positive": 0.8,
        "minimum_estimated_delta": 0.0,
        "require_consistent_slate_length": True,
        "maximum_estimator_spread": None,
    }
    values.update(overrides)
    return SlateExperimentCriteria(**values)


def _spec(estimator: str, criteria: SlateExperimentCriteria) -> SlateExperimentSpec:
    return SlateExperimentSpec(
        experiment_id=f"slate-exp-{estimator}",
        surface="recommend",
        hypothesis="candidate improves ranked-list reward",
        logging_policy_id="prod-control",
        candidate_policy_id="candidate-a",
        primary_estimator=estimator,
        criteria=criteria,
    )


def _positive_equal_length_rows() -> list[SlatePositionRecord]:
    rows = []
    for index in range(8):
        rows.append(
            _row(
                f"p{index}",
                0,
                f"a{index}",
                1.0,
                item_logging=0.5,
                item_target=0.8,
                cascade_logging=0.5,
                cascade_target=0.8,
            )
        )
    return rows


def _hand_rows_with_model() -> list[SlatePositionRecord]:
    return [
        _row(
            "s1",
            0,
            "a",
            1.0,
            item_logging=0.5,
            item_target=0.25,
            cascade_logging=0.5,
            cascade_target=0.25,
            q_values={"a": 0.6, "x": 0.2},
            target_dist={"a": 0.25, "x": 0.75},
        ),
        _row(
            "s1",
            1,
            "b",
            0.0,
            item_logging=0.4,
            item_target=0.8,
            cascade_logging=0.2,
            cascade_target=0.2,
            q_values={"a": 0.0, "b": 0.1, "x": 0.4},
            target_dist={"a": 0.0, "b": 0.8, "x": 0.2},
        ),
        _row(
            "s2",
            0,
            "c",
            0.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.5,
            cascade_target=0.5,
            q_values={"c": 0.2, "x": 0.4},
            target_dist={"c": 0.5, "x": 0.5},
        ),
        _row(
            "s2",
            1,
            "d",
            1.0,
            item_logging=0.5,
            item_target=0.25,
            cascade_logging=0.25,
            cascade_target=0.125,
            q_values={"c": 0.0, "d": 0.7, "x": 0.3},
            target_dist={"c": 0.0, "d": 0.25, "x": 0.75},
        ),
    ]


def test_iips_evidence_can_only_advance_to_controlled_online_experiment() -> None:
    result = evaluate_slate_experiment(
        _positive_equal_length_rows(),
        _spec("iips", _criteria(minimum_slates=6)),
        bootstrap_iterations=240,
    )

    decision = result["decision"]
    assert decision["eligible_for_online_test"] is True
    assert decision["automatic_activation"] is False
    assert decision["next_step"] == "controlled_online_experiment"
    assert decision["effective_sample_ratio"] == 1.0
    assert decision["effective_sample_ratio_basis"] == "minimum_item_position_effective_sample_ratio"
    assert decision["maximum_observed_importance_weight"] == 1.6


def test_rips_gate_uses_position_and_final_prefix_overlap_not_item_overlap() -> None:
    rows = [
        _row(
            "h1",
            0,
            "a",
            1.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.01,
            cascade_target=1.0,
        ),
        *[
            _row(
                f"h{index}",
                0,
                f"a{index}",
                0.0,
                item_logging=0.5,
                item_target=0.5,
                cascade_logging=1.0,
                cascade_target=0.1,
            )
            for index in range(2, 5)
        ],
    ]
    result = evaluate_slate_experiment(
        rows,
        _spec(
            "rips",
            _criteria(
                minimum_slates=4,
                minimum_effective_sample_ratio=0.5,
                maximum_importance_weight=20.0,
                minimum_probability_positive=0.0,
                minimum_estimated_delta=-100.0,
            ),
        ),
        bootstrap_iterations=160,
    )

    decision = result["decision"]
    assert decision["eligible_for_online_test"] is False
    assert decision["effective_sample_ratio_basis"] == (
        "minimum_of_position_and_final_prefix_cascade_effective_sample_ratio"
    )
    assert "effective_sample_ratio<0.5" in decision["blockers"]
    assert "importance_weight>20" in decision["blockers"]
    assert decision["automatic_activation"] is False


def test_inconsistent_slate_length_is_a_default_evidence_blocker() -> None:
    rows = [
        _row(
            "short",
            0,
            "a",
            1.0,
            item_logging=0.5,
            item_target=1.0,
            cascade_logging=0.5,
            cascade_target=1.0,
        ),
        _row(
            "long",
            0,
            "b",
            1.0,
            item_logging=0.5,
            item_target=0.25,
            cascade_logging=0.5,
            cascade_target=0.25,
        ),
        _row(
            "long",
            1,
            "c",
            1.0,
            item_logging=0.5,
            item_target=0.25,
            cascade_logging=0.25,
            cascade_target=0.125,
        ),
    ]
    result = evaluate_slate_experiment(
        rows,
        _spec(
            "iips",
            _criteria(
                minimum_effective_sample_ratio=0.0,
                maximum_importance_weight=10.0,
                minimum_probability_positive=0.0,
                minimum_estimated_delta=-10.0,
            ),
        ),
        bootstrap_iterations=160,
    )
    assert "inconsistent_slate_length" in result["decision"]["blockers"]


def test_product_can_explicitly_allow_variable_slate_length() -> None:
    rows = [
        _row(
            "short",
            0,
            "a",
            1.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.5,
            cascade_target=0.5,
        ),
        _row(
            "long",
            0,
            "b",
            1.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.5,
            cascade_target=0.5,
        ),
        _row(
            "long",
            1,
            "c",
            1.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.25,
            cascade_target=0.25,
        ),
    ]
    result = evaluate_slate_experiment(
        rows,
        _spec(
            "iips",
            _criteria(
                minimum_effective_sample_ratio=0.0,
                maximum_importance_weight=2.0,
                minimum_probability_positive=0.0,
                minimum_estimated_delta=-10.0,
                require_consistent_slate_length=False,
            ),
        ),
        bootstrap_iterations=160,
    )
    assert "inconsistent_slate_length" not in result["decision"]["blockers"]


def test_cascade_dr_primary_requires_complete_reward_model() -> None:
    result = evaluate_slate_experiment(
        _positive_equal_length_rows(),
        _spec(
            "cascade_dr",
            _criteria(
                minimum_slates=6,
                minimum_probability_positive=0.0,
                minimum_estimated_delta=-10.0,
            ),
        ),
        bootstrap_iterations=160,
    )

    assert result["decision"]["eligible_for_online_test"] is False
    assert "cascade_dr_unavailable" in result["decision"]["blockers"]
    assert result["decision"]["automatic_activation"] is False


def test_optional_estimator_agreement_gate_requires_cascade_dr_and_blocks_spread() -> None:
    result = evaluate_slate_experiment(
        _hand_rows_with_model(),
        _spec(
            "iips",
            _criteria(
                minimum_effective_sample_ratio=0.0,
                maximum_importance_weight=10.0,
                minimum_probability_positive=0.0,
                minimum_estimated_delta=-1.0,
                maximum_estimator_spread=0.01,
            ),
        ),
        bootstrap_iterations=180,
    )

    assert result["decision"]["eligible_for_online_test"] is False
    assert "estimator_spread>0.01" in result["decision"]["blockers"]
    assert result["decision"]["estimator_agreement_gate"] is True


def test_agreement_gate_cannot_silently_ignore_missing_cascade_dr() -> None:
    result = evaluate_slate_experiment(
        _positive_equal_length_rows(),
        _spec(
            "iips",
            _criteria(
                minimum_slates=6,
                maximum_estimator_spread=10.0,
            ),
        ),
        bootstrap_iterations=160,
    )
    assert "cascade_dr_unavailable_for_agreement_gate" in result["decision"]["blockers"]


def test_criteria_parsing_does_not_silently_coerce_fractional_integers_or_truthy_strings() -> None:
    with pytest.raises(ValueError, match="minimum_slates must be an integer"):
        SlateExperimentCriteria.from_dict(
            {
                "minimum_slates": 1.5,
                "minimum_effective_sample_ratio": 0.2,
                "maximum_importance_weight": 5.0,
                "minimum_probability_positive": 0.8,
            }
        )

    parsed = SlateExperimentCriteria.from_dict(
        {
            "minimum_slates": "2",
            "minimum_effective_sample_ratio": 0.2,
            "maximum_importance_weight": 0.8,
            "minimum_probability_positive": 0.8,
            "require_consistent_slate_length": "false",
        }
    )
    assert parsed.minimum_slates == 2
    assert parsed.maximum_importance_weight == 0.8
    assert parsed.require_consistent_slate_length is False

    with pytest.raises(ValueError, match="require_consistent_slate_length must be boolean"):
        SlateExperimentCriteria.from_dict(
            {
                "minimum_slates": 2,
                "minimum_effective_sample_ratio": 0.2,
                "maximum_importance_weight": 5.0,
                "minimum_probability_positive": 0.8,
                "require_consistent_slate_length": "nope",
            }
        )


def test_experiment_identity_must_match_slate_evidence() -> None:
    rows = _positive_equal_length_rows()
    spec = SlateExperimentSpec(
        experiment_id="mismatch",
        surface="search",
        hypothesis="candidate improves ranking",
        logging_policy_id="prod-control",
        candidate_policy_id="candidate-a",
        primary_estimator="iips",
        criteria=_criteria(),
    )
    with pytest.raises(ValueError, match="surface does not match experiment"):
        evaluate_slate_experiment(rows, spec)
