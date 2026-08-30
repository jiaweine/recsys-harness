from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

try:
    from scripts import optimizer_equal_budget_benchmark as benchmark
except ImportError:  # direct `python scripts/...py` execution
    import optimizer_equal_budget_benchmark as benchmark


def _initial_best_feasible_primary(landscape_name: str, seed: int) -> float | None:
    landscape = next(
        item for item in benchmark.landscapes() if item.name == landscape_name
    )
    _, cache_configs = benchmark._initial_design(landscape, seed)
    cache = benchmark._build_cache(landscape, cache_configs)
    feasible = [row for row in cache.values() if benchmark._is_feasible(row)]
    if not feasible:
        return None
    return max(float(row["objective"]) for row in feasible)


def correct_report(report: dict[str, Any]) -> dict[str, Any]:
    for row in report.get("runs", []):
        initial_best = _initial_best_feasible_primary(
            str(row["landscape"]),
            int(row["seed"]),
        )
        row["initial_best_feasible_primary"] = initial_best
        best = row.get("best_feasible_primary")
        calls = int(row.get("evaluator_calls", 0) or 0)
        row["primary_gain_per_evaluator_call"] = (
            (float(best) - float(initial_best)) / calls
            if best is not None and initial_best is not None and calls > 0
            else None
        )

    for backend, summary in (report.get("summary") or {}).items():
        values = [
            float(row["primary_gain_per_evaluator_call"])
            for row in report.get("runs", [])
            if row.get("backend") == backend
            and row.get("primary_gain_per_evaluator_call") is not None
        ]
        summary["mean_primary_gain_per_evaluator_call"] = (
            mean(values) if values else None
        )
    report["efficiency_metric_semantics"] = (
        "(best_feasible_primary - shared_initial_best_feasible_primary) / "
        "distinct_evaluator_calls"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    args = parser.parse_args()
    path = Path(args.path)
    report = json.loads(path.read_text(encoding="utf-8"))
    corrected = correct_report(report)
    path.write_text(
        json.dumps(corrected, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
