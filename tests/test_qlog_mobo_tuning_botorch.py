from __future__ import annotations

import math

import pytest

pytest.importorskip("botorch")

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms import qlog_mobo
from lingjing_harness.algorithms.optimizer_contracts import (
    OptimizerEvidenceContract,
    OptimizerOutcomeConstraint,
)


def _contract():
    return OptimizerEvidenceContract(
        surface="search",
        evidence_route="proxy",
        objective_names=("primary_objective", "domain_quality"),
        constraints=(
            OptimizerOutcomeConstraint("worse_share", "upper", 0.4),
            OptimizerOutcomeConstraint("worst_delta", "lower", -0.3),
        ),
    )


def _space():
    return qlog_mobo.MixedSearchSpace.build(
        base_config={"x": 0.5, "capability": "a"},
        dimensions=[
            core.EvolutionDimension(
                name="x",
                kind="continuous",
                group="independent",
                low=0.0,
                high=1.0,
            ),
            core.EvolutionDimension(
                name="capability",
                kind="capability",
                group="candidate",
                choices=("a", "b"),
            ),
        ],
        group_totals={},
    )


def _rows():
    return [
        {
            "config": {"x": x, "capability": capability},
            "report": {"quality": quality},
            "robustness": {"worse_share": worse, "worst_delta": worst},
            "objective": objective,
            "generation": 0,
            "source": "test",
        }
        for x, capability, objective, quality, worse, worst in [
            (0.05, "a", 0.31, 0.48, 0.32, -0.22),
            (0.25, "a", 0.40, 0.54, 0.26, -0.18),
            (0.45, "a", 0.47, 0.59, 0.20, -0.14),
            (0.55, "b", 0.50, 0.62, 0.18, -0.12),
            (0.75, "b", 0.46, 0.60, 0.24, -0.16),
            (0.95, "b", 0.34, 0.51, 0.35, -0.25),
        ]
    ]


def test_real_constrained_primary_qlognei_returns_legal_finite_candidate(monkeypatch):
    stack = qlog_mobo.load_botorch_stack()
    rows = _rows()
    contract = _contract()
    outcomes = [contract.outcome_values(row) for row in rows]
    reference, basis, count = qlog_mobo._reference_point(outcomes)

    monkeypatch.setattr(qlog_mobo, "ACQUISITION_RAW_SAMPLES", 24)
    monkeypatch.setattr(qlog_mobo, "ACQUISITION_RESTARTS", 3)
    monkeypatch.setattr(qlog_mobo, "MODEL_FIT_MAXITER", 30)

    candidate, metadata = qlog_mobo._candidate_from_acquisition(
        stack,
        space=_space(),
        rows=rows,
        contract=contract,
        seed=29,
        reference_point=reference,
        reference_point_basis=basis,
        reference_point_rows=count,
        mode="primary",
    )

    assert 0.0 <= candidate["x"] <= 1.0
    assert candidate["capability"] in {"a", "b"}
    assert metadata["acquisition"] == "qLogNoisyExpectedImprovement"
    assert metadata["acquisition_mode"] == "primary"
    assert metadata["acquisition_optimizer"] == "categorical_enumeration"
    assert metadata["model_outputs"] == 4
    assert metadata["posterior_mean_shape"][-1] == 4
    assert metadata["acquisition_raw_samples"] == 24
    assert metadata["acquisition_restarts"] == 3
    assert metadata["acquisition_value"] is not None
    assert math.isfinite(metadata["acquisition_value"])
