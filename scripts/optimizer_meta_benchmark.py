from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lingjing_harness.algorithms.optimizer_meta import (  # noqa: E402
    build_routing_context,
    optimizer_run_utility,
    rank_optimizer_backends,
)
from scripts import optimizer_equal_budget_benchmark as bench  # noqa: E402


FIXED_BACKENDS = ("native", "optuna", "optuna_motpe", "qlognehvi")


def _row_utility(row: dict[str, Any]) -> float:
    initial = row.get("initial_best_feasible_primary")
    final = row.get("best_feasible_primary")
    utility = optimizer_run_utility(
        initial_best_objective=float(initial) if initial is not None else None,
        final_best_objective=float(final) if final is not None else None,
        new_evaluations=int(row.get("evaluator_calls", 0) or 0),
        wall_seconds=float(row.get("wall_seconds", 0.0) or 0.0),
        evidence_route="proxy",
    )
    value = utility.get("utility")
    if value is None:
        raise ValueError("benchmark row is missing comparable optimizer evidence")
    return float(value)


def _shared_initial_observations(landscape: Any, seed: int):
    """Reconstruct the benchmark's already-shared initial design only.

    This calls the deterministic synthetic landscape directly, not CountingEvaluator,
    and therefore spends zero distinct optimizer evaluator calls. The fixed backends
    all received the exact same cache rows before their equal-budget search began.
    """

    _, cache_configs = bench._initial_design(landscape, seed)
    cache = bench._build_cache(landscape, cache_configs)
    observations: list[dict[str, Any]] = []
    for row in cache.values():
        observation = dict(row)
        observation["feasible"] = bool(bench._is_feasible(row))
        observations.append(observation)
    return cache, observations


def evaluate_meta_router(report: dict[str, Any]) -> dict[str, Any]:
    landscape_map = {row.name: row for row in bench.landscapes()}
    by_case: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for raw in report.get("runs", []):
        backend = str(raw.get("backend") or "")
        if backend not in FIXED_BACKENDS:
            continue
        key = (str(raw.get("landscape") or ""), int(raw.get("seed", 0)))
        by_case.setdefault(key, {})[backend] = dict(raw)

    cases: list[dict[str, Any]] = []
    selected_counts: Counter[str] = Counter()
    oracle_counts: Counter[str] = Counter()
    for (landscape_name, seed), fixed_rows in sorted(by_case.items()):
        missing = set(FIXED_BACKENDS) - set(fixed_rows)
        if missing:
            raise ValueError(
                f"meta benchmark requires all fixed backends for {landscape_name}/{seed}: "
                f"missing {sorted(missing)}"
            )
        landscape = landscape_map.get(landscape_name)
        if landscape is None:
            raise ValueError(f"unknown benchmark landscape: {landscape_name}")
        budgets = {int(row["evaluation_budget"]) for row in fixed_rows.values()}
        if len(budgets) != 1:
            raise ValueError("fixed backends do not share one evaluator budget")
        budget = budgets.pop()
        cache, observations = _shared_initial_observations(landscape, seed)
        if len(cache) != int(report.get("initial_design_size", 0)):
            raise ValueError("meta benchmark initial-design reconstruction drifted")
        context = build_routing_context(
            surface="search",
            evidence_route="proxy",
            evaluation_budget=budget,
            dimensions=landscape.dimensions,
            cache=cache,
            objective_count=2,
            constraint_count=len(bench.CONTRACT.constraints),
            landscape_observations=observations,
        )
        decision = rank_optimizer_backends(
            context,
            history=(),
            availability={backend: True for backend in FIXED_BACKENDS},
        )
        utilities = {backend: _row_utility(row) for backend, row in fixed_rows.items()}
        oracle_backend = max(
            FIXED_BACKENDS,
            key=lambda backend: (utilities[backend], -FIXED_BACKENDS.index(backend)),
        )
        selected_backend = decision.selected_backend
        selected_utility = utilities[selected_backend]
        oracle_utility = utilities[oracle_backend]
        regret = max(0.0, oracle_utility - selected_utility)
        selected_counts[selected_backend] += 1
        oracle_counts[oracle_backend] += 1
        selected_row = fixed_rows[selected_backend]
        cases.append(
            {
                "landscape": landscape_name,
                "seed": seed,
                "evaluation_budget": budget,
                "context": context.to_dict(),
                "context_key": context.context_key,
                "preobserved_landscape": context.landscape.to_dict(),
                "descriptor_evaluator_calls": 0,
                "selected_backend": selected_backend,
                "oracle_backend_by_cost_aware_utility": oracle_backend,
                "selected_utility": selected_utility,
                "oracle_utility": oracle_utility,
                "routing_regret": regret,
                "selected_primary_regret": selected_row.get("feasible_primary_regret"),
                "selected_hypervolume_regret": selected_row.get("hypervolume_regret"),
                "selected_wall_seconds": selected_row.get("wall_seconds"),
                "backend_utilities": utilities,
                "routing": decision.to_dict(),
            }
        )

    if not cases:
        raise ValueError("meta benchmark found no complete fixed-backend cases")
    regrets = [float(row["routing_regret"]) for row in cases]
    selected_utilities = [float(row["selected_utility"]) for row in cases]
    oracle_utilities = [float(row["oracle_utility"]) for row in cases]
    ratios = [
        selected / oracle if oracle > 1e-12 else 1.0
        for selected, oracle in zip(selected_utilities, oracle_utilities)
    ]
    for value in [*regrets, *selected_utilities, *oracle_utilities, *ratios]:
        if not math.isfinite(value):
            raise AssertionError("optimizer meta benchmark produced non-finite evidence")
    return {
        "benchmark": "optimizer_meta_router_from_equal_budget_evidence",
        "source_benchmark": report.get("benchmark"),
        "cases": cases,
        "summary": {
            "cases": len(cases),
            "descriptor_informed_cases": sum(
                bool(row["preobserved_landscape"].get("informative")) for row in cases
            ),
            "distinct_context_keys": len({row["context_key"] for row in cases}),
            "mean_routing_regret": mean(regrets),
            "max_routing_regret": max(regrets),
            "mean_selected_utility": mean(selected_utilities),
            "mean_oracle_utility": mean(oracle_utilities),
            "mean_oracle_utility_ratio": mean(ratios),
            "oracle_match_rate": sum(
                row["selected_backend"] == row["oracle_backend_by_cost_aware_utility"]
                for row in cases
            )
            / len(cases),
            "selected_backend_counts": dict(sorted(selected_counts.items())),
            "oracle_backend_counts": dict(sorted(oracle_counts.items())),
        },
        "hard_gate_semantics": (
            "accounting_and_finite_evidence_only; descriptors reuse shared initial-design "
            "evidence with zero new evaluator calls; routing ranking remains benchmark "
            "evidence, not a per-landscape must-win assertion"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    source = Path(args.input)
    report = json.loads(source.read_text(encoding="utf-8"))
    result = evaluate_meta_router(report)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
