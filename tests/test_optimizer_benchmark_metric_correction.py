from __future__ import annotations

from scripts import correct_optimizer_benchmark_metrics as metrics
from scripts import optimizer_equal_budget_benchmark as benchmark


def test_corrected_efficiency_metric_is_incremental_gain_per_expensive_call(monkeypatch):
    monkeypatch.setattr(benchmark, "GRID_STEPS", 11)
    report = benchmark.run_benchmark(backends=("native",), seeds=(17,))
    corrected = metrics.correct_report(report)

    assert corrected["efficiency_metric_semantics"].startswith(
        "(best_feasible_primary - shared_initial_best_feasible_primary)"
    )
    for row in corrected["runs"]:
        expected = (
            row["best_feasible_primary"] - row["initial_best_feasible_primary"]
        ) / row["evaluator_calls"]
        assert row["primary_gain_per_evaluator_call"] == expected
    assert corrected["summary"]["native"][
        "mean_primary_gain_per_evaluator_call"
    ] is not None
