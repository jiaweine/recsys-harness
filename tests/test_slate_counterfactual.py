from __future__ import annotations

import math

import pytest

from lingjing_harness.slate_counterfactual import (
    SlatePositionRecord,
    evaluate_slate_off_policy,
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
    surface: str = "recommend",
) -> SlatePositionRecord:
    return SlatePositionRecord(
        slate_id=slate_id,
        surface=surface,
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


def _hand_computed_rows() -> list[SlatePositionRecord]:
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


def test_iips_rips_and_cascade_dr_match_hand_computed_slates() -> None:
    report = evaluate_slate_off_policy(_hand_computed_rows(), bootstrap_iterations=200)

    assert report["available"] is True
    assert report["slates"] == 2
    assert report["positions"] == 4
    assert report["estimators"]["logged_mean"]["value"] == 1.0
    assert report["estimators"]["iips"]["value"] == 0.5
    assert report["estimators"]["rips"]["value"] == 0.5
    assert math.isclose(report["estimators"]["cascade_dr"]["value"], 0.565)
    assert report["estimators"]["iips"]["delta_vs_logged"] == -0.5
    assert report["estimators"]["rips"]["delta_vs_logged"] == -0.5
    assert math.isclose(
        report["estimators"]["cascade_dr"]["delta_vs_logged"],
        -0.435,
    )


def test_cascade_dr_uses_previous_prefix_weight_for_direct_method_term() -> None:
    report = evaluate_slate_off_policy(_hand_computed_rows(), bootstrap_iterations=120)
    # s1: .5*(1-.6)+1*.3 + 1*(0-.1)+.5*.16 = .48
    # s2: 1*(0-.2)+1*.3 + .5*(1-.7)+1*.4 = .65
    assert math.isclose(report["estimators"]["cascade_dr"]["value"], (0.48 + 0.65) / 2)


def test_whole_slate_bootstrap_does_not_resample_positions_independently() -> None:
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
    report = evaluate_slate_off_policy(rows, bootstrap_iterations=1000)
    confidence = report["confidence"]["iips"]

    # short slate delta = 2 - 1 = +1; long slate delta = 1 - 2 = -1.
    # With two whole-slate draws, bootstrap deltas can only be {-1, 0, +1}.
    assert confidence["bootstrap_unit"] == "whole_slate"
    assert confidence["delta"] == 0.0
    assert confidence["ci95"] == [-1.0, 1.0]
    assert report["slate_lengths"] == [1, 2]


def test_heavy_tail_overlap_is_visible_at_position_and_final_prefix_levels() -> None:
    rows = [
        _row(
            "h1",
            0,
            "a",
            1.0,
            item_logging=0.01,
            item_target=1.0,
            cascade_logging=0.01,
            cascade_target=1.0,
        ),
        *[
            _row(
                f"h{index}",
                0,
                f"a{index}",
                0.0,
                item_logging=1.0,
                item_target=0.1,
                cascade_logging=1.0,
                cascade_target=0.1,
            )
            for index in range(2, 5)
        ],
    ]
    report = evaluate_slate_off_policy(rows, bootstrap_iterations=160)
    diagnostics = report["diagnostics"]

    assert diagnostics["item_position_weight_max"] == 100.0
    assert diagnostics["cascade_weight_max"] == 100.0
    assert diagnostics["minimum_item_position_effective_sample_ratio"] < 0.3
    assert diagnostics["minimum_cascade_effective_sample_ratio"] < 0.3
    assert diagnostics["final_prefix_effective_sample_ratio"] < 0.3
    assert diagnostics["heavy_tail_detected"] is True


def test_incomplete_reward_model_only_disables_cascade_dr() -> None:
    rows = _hand_computed_rows()
    first = rows[0].to_dict()
    first.pop("q_values")
    first.pop("target_action_distribution")
    partial = [SlatePositionRecord.from_dict(first), *rows[1:]]

    report = evaluate_slate_off_policy(partial, bootstrap_iterations=120)

    assert report["estimators"]["iips"]["available"] is True
    assert report["estimators"]["rips"]["available"] is True
    assert report["estimators"]["cascade_dr"]["available"] is False
    assert report["confidence"]["cascade_dr"]["reason"] == "reward_model_incomplete"
    assert report["diagnostics"]["reward_model_coverage"] == 0.75


def test_target_prefix_probability_must_match_conditional_target_distribution() -> None:
    rows = _hand_computed_rows()
    broken = rows[1].to_dict()
    broken["target_cascade_propensity"] = 0.1
    records = [rows[0], SlatePositionRecord.from_dict(broken), *rows[2:]]

    with pytest.raises(ValueError, match="inconsistent with target_action_distribution"):
        evaluate_slate_off_policy(records)


def test_target_distribution_cannot_reintroduce_an_action_already_in_prefix() -> None:
    rows = _hand_computed_rows()
    broken = rows[1].to_dict()
    broken["target_action_distribution"] = {"a": 0.1, "b": 0.8, "x": 0.1}
    broken["q_values"] = {"a": 0.0, "b": 0.1, "x": 0.4}
    records = [rows[0], SlatePositionRecord.from_dict(broken), *rows[2:]]

    with pytest.raises(ValueError, match="assigns mass to a prior action"):
        evaluate_slate_off_policy(records)


def test_q_values_must_cover_target_action_distribution() -> None:
    with pytest.raises(ValueError, match="cover every action"):
        _row(
            "bad",
            0,
            "a",
            1.0,
            item_logging=0.5,
            item_target=0.5,
            cascade_logging=0.5,
            cascade_target=0.5,
            q_values={"a": 0.4},
            target_dist={"a": 0.5, "x": 0.5},
        )


def test_slate_positions_must_be_contiguous_and_actions_unique() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        evaluate_slate_off_policy(
            [
                _row(
                    "gap",
                    1,
                    "a",
                    1.0,
                    item_logging=0.5,
                    item_target=0.5,
                    cascade_logging=0.5,
                    cascade_target=0.5,
                )
            ]
        )

    with pytest.raises(ValueError, match="must not repeat"):
        evaluate_slate_off_policy(
            [
                _row(
                    "dup",
                    0,
                    "a",
                    1.0,
                    item_logging=0.5,
                    item_target=0.5,
                    cascade_logging=0.5,
                    cascade_target=0.5,
                ),
                _row(
                    "dup",
                    1,
                    "a",
                    0.0,
                    item_logging=0.5,
                    item_target=0.5,
                    cascade_logging=0.25,
                    cascade_target=0.25,
                ),
            ]
        )


def test_mixed_surface_or_policy_identity_is_rejected() -> None:
    rows = _hand_computed_rows()
    mixed_surface = rows[-1].to_dict()
    mixed_surface["surface"] = "search"
    with pytest.raises(ValueError, match="exactly one surface"):
        evaluate_slate_off_policy([*rows[:-1], SlatePositionRecord.from_dict(mixed_surface)])

    mixed_policy = rows[-1].to_dict()
    mixed_policy["target_policy_id"] = "candidate-b"
    with pytest.raises(ValueError, match="exactly one target_policy_id"):
        evaluate_slate_off_policy([*rows[:-1], SlatePositionRecord.from_dict(mixed_policy)])


def test_slate_report_is_deterministic_for_identical_evidence() -> None:
    rows = _hand_computed_rows()
    first = evaluate_slate_off_policy(rows, bootstrap_iterations=220)
    second = evaluate_slate_off_policy(rows, bootstrap_iterations=220)
    assert first == second
