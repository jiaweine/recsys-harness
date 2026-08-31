from scripts.optimizer_meta_benchmark import FIXED_BACKENDS, evaluate_meta_router


def _row(backend: str, *, final: float, wall: float):
    return {
        "backend": backend,
        "landscape": "smooth_mixed_constrained",
        "seed": 17,
        "evaluation_budget": 10,
        "evaluator_calls": 10,
        "initial_best_feasible_primary": 0.5,
        "best_feasible_primary": final,
        "wall_seconds": wall,
        "feasible_primary_regret": 0.7 - final,
        "hypervolume_regret": 0.02,
    }


def test_meta_benchmark_reuses_equal_budget_fixed_backend_and_initial_design_evidence():
    report = {
        "benchmark": "equal_distinct_evaluator_budget",
        "initial_design_size": 4,
        "runs": [
            _row("native", final=0.66, wall=0.01),
            _row("optuna", final=0.64, wall=0.08),
            _row("optuna_motpe", final=0.63, wall=0.09),
            _row("qlognehvi", final=0.68, wall=35.0),
        ],
    }

    result = evaluate_meta_router(report)
    case = result["cases"][0]

    assert set(case["backend_utilities"]) == set(FIXED_BACKENDS)
    assert case["descriptor_evaluator_calls"] == 0
    assert case["preobserved_landscape"]["informative"] is True
    assert case["preobserved_landscape"]["source"] == "preobserved_rows_only"
    assert case["selected_backend"] == "native"
    assert case["oracle_backend_by_cost_aware_utility"] == "native"
    assert case["routing_regret"] == 0.0
    assert result["summary"]["descriptor_informed_cases"] == 1
    assert result["summary"]["oracle_match_rate"] == 1.0
    assert result["hard_gate_semantics"].startswith("accounting_and_finite_evidence_only")


def test_meta_benchmark_rejects_incomplete_backend_comparison():
    report = {
        "benchmark": "equal_distinct_evaluator_budget",
        "initial_design_size": 4,
        "runs": [_row("native", final=0.58, wall=0.01)],
    }

    try:
        evaluate_meta_router(report)
    except ValueError as exc:
        assert "requires all fixed backends" in str(exc)
    else:
        raise AssertionError("incomplete fixed-backend evidence must fail closed")
