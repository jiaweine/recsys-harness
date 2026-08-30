from __future__ import annotations

import math

import pytest

from scripts import optimizer_equal_budget_benchmark as benchmark


def test_hypervolume_2d_matches_exact_union_area():
    # Rectangles [0,1]x[0,10] and [0,2]x[0,5] have union area 15.
    value = benchmark.hypervolume_2d([(1.0, 10.0), (2.0, 5.0)], (0.0, 0.0))

    assert value == pytest.approx(15.0)


def test_pareto_sweep_removes_dominated_feasible_rows():
    landscape = benchmark.landscapes()[0]

    def row(primary: float, quality: float, worse: float = 0.1, worst: float = -0.1):
        return {
            "config": {"x": 0.5, "y": 0.5, "capability": "hybrid"},
            "objective": primary,
            "report": {"quality": quality},
            "robustness": {"worse_share": worse, "worst_delta": worst},
        }

    assert landscape.name == "smooth_mixed_constrained"
    frontier = benchmark._pareto_points(
        [
            row(1.0, 1.0),
            row(0.8, 1.1),
            row(0.7, 0.9),
            row(1.2, 0.7),
            row(1.5, 1.5, worse=0.9),  # infeasible, excluded
        ]
    )

    assert frontier == [(0.8, 1.1), (1.0, 1.0), (1.2, 0.7)]


def test_counting_evaluator_accepts_explicit_optimizer_contract():
    evaluator = benchmark.CountingEvaluator(benchmark.landscapes()[0])

    benchmark.attach_optimizer_evidence_contract(evaluator, benchmark.CONTRACT)
    report, robust, score = evaluator(
        {"x": 0.5, "y": 0.4, "capability": "hybrid"}
    )

    assert evaluator.calls == 1
    assert evaluator._optimizer_evidence_contract is benchmark.CONTRACT
    assert math.isfinite(float(report["quality"]))
    assert math.isfinite(float(robust["worse_share"]))
    assert math.isfinite(float(score))


def test_native_benchmark_obeys_same_distinct_evaluator_budget(monkeypatch):
    # Keep the unit test cheap; the dedicated workflow runs the 101x101 oracle and
    # all four real optimizers with pinned optional dependencies. A 21x21 grid is
    # deliberately only an approximate oracle, so an off-grid continuous candidate
    # may exceed it slightly; the contract here is accounting and metric finiteness.
    monkeypatch.setattr(benchmark, "GRID_STEPS", 21)
    report = benchmark.run_benchmark(backends=("native",), seeds=(17,))

    assert report["benchmark"] == "equal_distinct_evaluator_budget"
    assert report["summary"]["native"]["runs"] == 2
    assert report["summary"]["native"]["feasible_run_rate"] == 1.0
    for row in report["runs"]:
        assert row["evaluation_budget"] == 10
        assert 0 < row["evaluator_calls"] <= row["evaluation_budget"]
        assert row["feasible_found"] is True
        assert math.isfinite(float(row["feasible_primary_regret"]))
        assert row["hypervolume_regret"] >= -1e-9
