from __future__ import annotations

from lingjing_harness.algorithms.statistical_trust import PairedTrustPolicy


def test_deterministic_positive_paired_evidence_can_pass_with_two_samples() -> None:
    policy = PairedTrustPolicy()
    result = policy.assess(
        {
            "available": True,
            "samples": 2,
            "delta": 0.05,
            "ci95": [0.05, 0.05],
            "probability_positive": 1.0,
        }
    )
    assert result["passed"] is True
    assert result["blockers"] == []


def test_wide_interval_blocks_durable_trust_and_estimates_more_samples() -> None:
    policy = PairedTrustPolicy()
    result = policy.assess(
        {
            "available": True,
            "samples": 4,
            "delta": 0.003,
            "ci95": [-0.005, 0.095],
            "probability_positive": 0.80,
        }
    )
    assert result["passed"] is False
    assert "paired_confidence_underpowered" in result["blockers"]
    assert result["approx_required_samples"] > 4


def test_noninferiority_margin_blocks_material_downside_tail() -> None:
    policy = PairedTrustPolicy(noninferiority_margin=0.01)
    result = policy.assess(
        {
            "available": True,
            "samples": 20,
            "delta": 0.02,
            "ci95": [-0.03, 0.07],
            "probability_positive": 0.90,
        }
    )
    assert result["passed"] is False
    assert "paired_ci_crosses_noninferiority_margin" in result["blockers"]
