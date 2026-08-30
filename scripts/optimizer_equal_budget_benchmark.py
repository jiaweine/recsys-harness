from __future__ import annotations

import argparse
import copy
import json
import math
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from random import Random
from statistics import mean
from typing import Any, Callable, Iterator
from unittest.mock import patch

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms import optimizer_backends
from lingjing_harness.algorithms.optimizer_contracts import (
    OptimizerEvidenceContract,
    OptimizerOutcomeConstraint,
    attach_optimizer_evidence_contract,
)
from lingjing_harness.algorithms.qlog_mobo import qlognehvi_evolution_loop


DEFAULT_BACKENDS = ("native", "optuna", "optuna_motpe", "qlognehvi")
BENCHMARK_POPULATION_SIZE = 8
BENCHMARK_GENERATIONS = 3
INITIAL_DESIGN_SIZE = 4
GRID_STEPS = 101


@dataclass(frozen=True, slots=True)
class Landscape:
    name: str
    base_config: dict[str, Any]
    dimensions: tuple[core.EvolutionDimension, ...]
    group_totals: dict[str, float]
    categories: tuple[str, ...]
    score: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]]
    oracle_reference_point: tuple[float, float]


@dataclass(slots=True)
class CountingEvaluator:
    landscape: Landscape
    calls: int = 0
    _optimizer_evidence_contract: OptimizerEvidenceContract | None = None

    def __call__(
        self, config: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, float], float]:
        self.calls += 1
        return self.landscape.score(config)


def _constraint_contract() -> OptimizerEvidenceContract:
    return OptimizerEvidenceContract(
        surface="search",
        evidence_route="proxy",
        objective_names=("primary_objective", "domain_quality"),
        constraints=(
            OptimizerOutcomeConstraint("worse_share", "upper", 0.40),
            OptimizerOutcomeConstraint("worst_delta", "lower", -0.30),
        ),
    )


CONTRACT = _constraint_contract()


def _bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _deterministic_ripple(x: float, y: float, category_index: int) -> float:
    # Fixed pseudo-noise / local roughness. Repeated evaluation of the same config is
    # exactly reproducible, so duplicate proposals cannot gain extra samples.
    return 0.012 * math.sin(19.0 * x + 7.0 * y + 1.9 * category_index)


def _smooth_mixed_score(config: dict[str, Any]):
    x = float(config["x"])
    y = float(config["y"])
    category = str(config["capability"])
    category_index = {"lexical": 0, "hybrid": 1, "semantic": 2}[category]
    primary_bonus = {"lexical": -0.035, "hybrid": 0.055, "semantic": 0.015}[category]
    quality_bonus = {"lexical": -0.01, "hybrid": 0.025, "semantic": 0.045}[category]
    primary = (
        0.93
        - 1.45 * (x - 0.72) ** 2
        - 1.10 * (y - 0.28) ** 2
        + primary_bonus
        + _deterministic_ripple(x, y, category_index)
    )
    quality = (
        0.89
        - 1.05 * (x - 0.58) ** 2
        - 0.85 * (y - 0.42) ** 2
        + quality_bonus
        - 0.006 * math.cos(13.0 * x - 5.0 * y)
    )
    category_relief = {"lexical": 0.0, "hybrid": 0.035, "semantic": 0.02}[category]
    worse_share = _bounded(
        0.13
        + 0.72 * max(0.0, x + 0.72 * y - (0.92 + category_relief)) ** 2
        + 0.22 * max(0.0, 0.18 - y) ** 2
    )
    worst_delta = -(
        0.075
        + 0.86 * max(0.0, x - (0.84 + category_relief)) ** 2
        + 0.65 * max(0.0, 0.14 - y) ** 2
    )
    report = {"quality": quality, "business_reward": primary}
    robust = {"worse_share": worse_share, "worst_delta": worst_delta}
    objective = primary + 0.055 * quality - 0.025 * worse_share + 0.01 * min(0.0, worst_delta)
    return report, robust, objective


def _interaction_basin_score(config: dict[str, Any]):
    x = float(config["x"])
    y = float(config["y"])
    category = str(config["capability"])
    category_index = {"lexical": 0, "hybrid": 1, "semantic": 2}[category]
    centers = {
        "lexical": ((0.22, 0.74), 0.72, 0.76),
        "hybrid": ((0.76, 0.24), 0.99, 0.88),
        "semantic": ((0.57, 0.60), 0.87, 0.96),
    }
    (cx, cy), primary_peak, quality_peak = centers[category]
    distance = (x - cx) ** 2 + 0.85 * (y - cy) ** 2
    local_basin = 0.085 * math.exp(
        -45.0 * ((x - 0.35) ** 2 + (y - 0.36) ** 2)
    )
    interaction = (
        0.035 * math.sin(8.0 * x * y + 1.3 * category_index)
        + _deterministic_ripple(x, y, category_index)
    )
    primary = primary_peak - 1.55 * distance + local_basin + interaction
    quality = quality_peak - 1.18 * distance + 0.025 * math.cos(9.0 * x - 4.0 * y)
    category_relief = {"lexical": -0.01, "hybrid": 0.045, "semantic": 0.015}[category]
    worse_share = _bounded(
        0.12
        + 0.95 * max(0.0, x - (0.73 + category_relief)) ** 2
        + 0.55 * max(0.0, 0.16 - y) ** 2
    )
    worst_delta = -(
        0.09
        + 1.05 * max(0.0, x - (0.79 + category_relief)) ** 2
        + 0.52 * max(0.0, 0.13 - y) ** 2
    )
    report = {"quality": quality, "business_reward": primary}
    robust = {"worse_share": worse_share, "worst_delta": worst_delta}
    objective = primary + 0.045 * quality - 0.03 * worse_share + 0.012 * min(0.0, worst_delta)
    return report, robust, objective


def landscapes() -> tuple[Landscape, ...]:
    dimensions = (
        core.EvolutionDimension(
            name="x",
            kind="continuous",
            group="independent",
            low=0.0,
            high=1.0,
            relative_step=0.18,
        ),
        core.EvolutionDimension(
            name="y",
            kind="continuous",
            group="independent",
            low=0.0,
            high=1.0,
            relative_step=0.18,
        ),
        core.EvolutionDimension(
            name="capability",
            kind="capability",
            group="candidate",
            choices=("lexical", "hybrid", "semantic"),
        ),
    )
    return (
        Landscape(
            name="smooth_mixed_constrained",
            base_config={"x": 0.35, "y": 0.55, "capability": "lexical"},
            dimensions=dimensions,
            group_totals={},
            categories=("lexical", "hybrid", "semantic"),
            score=_smooth_mixed_score,
            oracle_reference_point=(0.0, 0.0),
        ),
        Landscape(
            name="interaction_basin_constrained",
            base_config={"x": 0.32, "y": 0.38, "capability": "lexical"},
            dimensions=dimensions,
            group_totals={},
            categories=("lexical", "hybrid", "semantic"),
            score=_interaction_basin_score,
            oracle_reference_point=(0.0, 0.0),
        ),
    )


def _initial_design(landscape: Landscape, seed: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = Random(seed ^ 0x5EED)
    rows: list[dict[str, Any]] = [dict(landscape.base_config)]
    anchors = (
        (0.12, 0.18),
        (0.82, 0.22),
        (0.28, 0.78),
        (0.72, 0.72),
        (0.48, 0.48),
        (0.92, 0.52),
        (0.54, 0.10),
    )
    for index, (x, y) in enumerate(anchors):
        category = landscape.categories[(index + seed) % len(landscape.categories)]
        jitter_x = (rng.random() - 0.5) * 0.04
        jitter_y = (rng.random() - 0.5) * 0.04
        rows.append(
            {
                "x": _bounded(x + jitter_x),
                "y": _bounded(y + jitter_y),
                "capability": category,
            }
        )
    rows = core._unique_configs(rows)[:BENCHMARK_POPULATION_SIZE]
    if len(rows) < BENCHMARK_POPULATION_SIZE:
        raise RuntimeError("benchmark initial population lost uniqueness")
    cache_configs = rows[:INITIAL_DESIGN_SIZE]
    return rows, cache_configs


def _build_cache(landscape: Landscape, configs: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    cache: dict[Any, dict[str, Any]] = {}
    for config in configs:
        report, robust, objective = landscape.score(config)
        row = {
            "config": dict(config),
            "report": report,
            "robustness": robust,
            "objective": round(float(objective), 7),
            "generation": 0,
            "source": "shared_initial_design",
        }
        cache[core._config_key(config)] = row
    return cache


def _is_feasible(row: dict[str, Any]) -> bool:
    outcomes = CONTRACT.outcome_values(row)
    return all(value <= 0.0 for value in outcomes[2:])


def _pareto_points(rows: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Return the 2D maximization frontier in O(n log n)."""

    points: set[tuple[float, float]] = set()
    for row in rows:
        if not _is_feasible(row):
            continue
        primary, quality, *_ = CONTRACT.outcome_values(row)
        points.add((float(primary), float(quality)))
    if not points:
        return []
    best_quality = -math.inf
    frontier_desc: list[tuple[float, float]] = []
    for primary, quality in sorted(points, key=lambda point: (-point[0], -point[1])):
        if quality > best_quality:
            frontier_desc.append((primary, quality))
            best_quality = quality
    return list(reversed(frontier_desc))


def hypervolume_2d(points: list[tuple[float, float]], reference: tuple[float, float]) -> float:
    """Exact dominated hypervolume for a nondominated 2D maximization frontier."""

    ref_x, ref_y = reference
    frontier = sorted(
        {
            (x, y)
            for x, y in points
            if x > ref_x and y > ref_y and math.isfinite(x) and math.isfinite(y)
        }
    )
    if not frontier:
        return 0.0
    volume = 0.0
    previous_x = ref_x
    for x, y in frontier:
        volume += max(0.0, x - previous_x) * max(0.0, y - ref_y)
        previous_x = x
    return volume


def _oracle(landscape: Landscape) -> dict[str, float]:
    feasible_rows: list[dict[str, Any]] = []
    for category in landscape.categories:
        for xi in range(GRID_STEPS):
            x = xi / (GRID_STEPS - 1)
            for yi in range(GRID_STEPS):
                y = yi / (GRID_STEPS - 1)
                config = {"x": x, "y": y, "capability": category}
                report, robust, objective = landscape.score(config)
                row = {
                    "config": config,
                    "report": report,
                    "robustness": robust,
                    "objective": objective,
                }
                if _is_feasible(row):
                    feasible_rows.append(row)
    if not feasible_rows:
        raise RuntimeError(f"oracle found no feasible points for {landscape.name}")
    best_primary = max(float(row["objective"]) for row in feasible_rows)
    hv = hypervolume_2d(
        _pareto_points(feasible_rows),
        landscape.oracle_reference_point,
    )
    return {
        "best_feasible_primary": best_primary,
        "feasible_hypervolume": hv,
        "grid_points": len(landscape.categories) * GRID_STEPS * GRID_STEPS,
    }


@contextmanager
def _benchmark_core_budget() -> Iterator[None]:
    with (
        patch.object(core, "POPULATION_SIZE", BENCHMARK_POPULATION_SIZE),
        patch.object(core, "MAX_GENERATIONS", BENCHMARK_GENERATIONS),
        patch.object(optimizer_backends.core, "POPULATION_SIZE", BENCHMARK_POPULATION_SIZE),
        patch.object(optimizer_backends.core, "MAX_GENERATIONS", BENCHMARK_GENERATIONS),
    ):
        yield


def _run_backend(
    *,
    backend: str,
    landscape: Landscape,
    seed: int,
    population: list[dict[str, Any]],
    cache: dict[Any, dict[str, Any]],
    evaluation_budget: int,
) -> dict[str, Any]:
    evaluator = CountingEvaluator(landscape)
    attach_optimizer_evidence_contract(evaluator, CONTRACT)
    rng = Random(seed)
    started = time.perf_counter()
    if backend == "native":
        rows, _ = core._evolution_loop(
            base_config=landscape.base_config,
            population=copy.deepcopy(population),
            dimensions=list(landscape.dimensions),
            group_totals=dict(landscape.group_totals),
            evaluate=evaluator,
            rng=rng,
            cache=copy.deepcopy(cache),
        )
    elif backend == "optuna":
        rows, _ = optimizer_backends._optuna_evolution_loop(
            base_config=landscape.base_config,
            population=copy.deepcopy(population),
            dimensions=list(landscape.dimensions),
            group_totals=dict(landscape.group_totals),
            evaluate=evaluator,
            rng=rng,
            cache=copy.deepcopy(cache),
        )
    elif backend == "optuna_motpe":
        rows, _ = optimizer_backends._optuna_motpe_evolution_loop(
            base_config=landscape.base_config,
            population=copy.deepcopy(population),
            dimensions=list(landscape.dimensions),
            group_totals=dict(landscape.group_totals),
            evaluate=evaluator,
            rng=rng,
            cache=copy.deepcopy(cache),
        )
    elif backend == "qlognehvi":
        rows, _ = qlognehvi_evolution_loop(
            base_config=landscape.base_config,
            population=copy.deepcopy(population),
            dimensions=list(landscape.dimensions),
            group_totals=dict(landscape.group_totals),
            evaluate=evaluator,
            rng=rng,
            cache=copy.deepcopy(cache),
            evaluation_budget=evaluation_budget,
        )
    else:
        raise ValueError(f"unknown benchmark backend: {backend}")
    elapsed = time.perf_counter() - started
    if evaluator.calls > evaluation_budget:
        raise AssertionError(
            f"{backend} exceeded evaluator budget: {evaluator.calls}>{evaluation_budget}"
        )

    feasible = [row for row in rows if _is_feasible(row)]
    best = max(feasible, key=lambda row: float(row["objective"])) if feasible else None
    all_new = [row for row in rows if row.get("source") != "shared_initial_design"]
    violated_new = sum(1 for row in all_new if not _is_feasible(row))
    hypervolume = hypervolume_2d(
        _pareto_points(rows), landscape.oracle_reference_point
    )
    result = {
        "backend": backend,
        "landscape": landscape.name,
        "seed": seed,
        "evaluation_budget": evaluation_budget,
        "evaluator_calls": evaluator.calls,
        "budget_utilization": evaluator.calls / max(1, evaluation_budget),
        "wall_seconds": elapsed,
        "feasible_found": best is not None,
        "best_feasible_primary": float(best["objective"]) if best else None,
        "best_feasible_quality": (
            float(best["report"].get("quality", 0.0)) if best else None
        ),
        "feasible_hypervolume": hypervolume,
        "new_candidate_constraint_violation_rate": (
            violated_new / len(all_new) if all_new else 0.0
        ),
        "evaluated_rows": len(rows),
    }
    if best is not None:
        result["best_config"] = dict(best["config"])
    provenance = rows[0].get("optimizer_provenance") if rows else None
    if isinstance(provenance, dict):
        result["optimizer_provenance"] = {
            "new_evaluations": provenance.get("new_evaluations"),
            "duplicate_proposals": provenance.get("duplicate_proposals"),
            "acquisition_steps": provenance.get("acquisition_steps"),
            "reference_point_basis": provenance.get("reference_point_basis"),
            "last_acquisition_optimizer": (
                (provenance.get("last_acquisition") or {}).get("acquisition_optimizer")
            ),
        }
    return result


def run_benchmark(
    *,
    backends: tuple[str, ...] = DEFAULT_BACKENDS,
    seeds: tuple[int, ...] = (17,),
) -> dict[str, Any]:
    unknown = set(backends) - set(DEFAULT_BACKENDS)
    if unknown:
        raise ValueError(f"unsupported benchmark backends: {sorted(unknown)}")
    suites = landscapes()
    oracles = {landscape.name: _oracle(landscape) for landscape in suites}
    runs: list[dict[str, Any]] = []

    with _benchmark_core_budget():
        for landscape in suites:
            for seed in seeds:
                population, cache_configs = _initial_design(landscape, seed)
                cache = _build_cache(landscape, cache_configs)
                budget = optimizer_backends._native_distinct_evaluation_budget(
                    population, cache
                )
                if budget <= 0:
                    raise AssertionError("benchmark must expose a positive evaluator budget")
                for backend in backends:
                    row = _run_backend(
                        backend=backend,
                        landscape=landscape,
                        seed=seed,
                        population=population,
                        cache=cache,
                        evaluation_budget=budget,
                    )
                    oracle = oracles[landscape.name]
                    best_primary = row["best_feasible_primary"]
                    row["feasible_primary_regret"] = (
                        oracle["best_feasible_primary"] - best_primary
                        if best_primary is not None
                        else None
                    )
                    row["hypervolume_regret"] = max(
                        0.0,
                        oracle["feasible_hypervolume"] - row["feasible_hypervolume"],
                    )
                    row["primary_gain_per_evaluator_call"] = (
                        best_primary / max(1, row["evaluator_calls"])
                        if best_primary is not None
                        else None
                    )
                    runs.append(row)

    summary: dict[str, Any] = {}
    for backend in backends:
        subset = [row for row in runs if row["backend"] == backend]
        regrets = [
            float(row["feasible_primary_regret"])
            for row in subset
            if row["feasible_primary_regret"] is not None
        ]
        hv_regrets = [float(row["hypervolume_regret"]) for row in subset]
        summary[backend] = {
            "runs": len(subset),
            "feasible_run_rate": sum(1 for row in subset if row["feasible_found"]) / max(1, len(subset)),
            "mean_feasible_primary_regret": mean(regrets) if regrets else None,
            "mean_hypervolume_regret": mean(hv_regrets) if hv_regrets else None,
            "mean_constraint_violation_rate": mean(
                float(row["new_candidate_constraint_violation_rate"])
                for row in subset
            ),
            "mean_evaluator_calls": mean(float(row["evaluator_calls"]) for row in subset),
            "mean_wall_seconds": mean(float(row["wall_seconds"]) for row in subset),
        }

    for row in runs:
        if row["evaluator_calls"] > row["evaluation_budget"]:
            raise AssertionError("backend exceeded equal-budget contract")
        if not math.isfinite(float(row["wall_seconds"])) or row["wall_seconds"] < 0.0:
            raise AssertionError("invalid benchmark wall time")

    return {
        "benchmark": "equal_distinct_evaluator_budget",
        "population_size": BENCHMARK_POPULATION_SIZE,
        "generations": BENCHMARK_GENERATIONS,
        "initial_design_size": INITIAL_DESIGN_SIZE,
        "backends": list(backends),
        "seeds": list(seeds),
        "landscapes": [landscape.name for landscape in suites],
        "oracles": oracles,
        "runs": runs,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--backends",
        default=",".join(DEFAULT_BACKENDS),
        help="comma-separated backend names",
    )
    parser.add_argument(
        "--seeds",
        default="17",
        help="comma-separated integer seeds",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    backends = tuple(value.strip() for value in args.backends.split(",") if value.strip())
    seeds = tuple(int(value.strip()) for value in args.seeds.split(",") if value.strip())
    report = run_benchmark(backends=backends, seeds=seeds)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
