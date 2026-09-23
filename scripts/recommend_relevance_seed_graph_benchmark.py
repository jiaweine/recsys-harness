from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import time
from typing import Callable

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.recommend_temporal_graph import TemporalGraphSnapshot
from lingjing_harness.domain import Catalog, Interaction, Item


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3),
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3),
    }


def _paired_timed(
    legacy_fn: Callable[[], object],
    optimized_fn: Callable[[], object],
    repeats: int,
) -> tuple[list[float], list[float]]:
    legacy: list[float] = []
    optimized: list[float] = []
    gc_enabled = gc.isenabled()
    gc.disable()
    try:
        for index in range(repeats):
            ordered = (
                ((legacy_fn, legacy), (optimized_fn, optimized))
                if index % 2 == 0
                else ((optimized_fn, optimized), (legacy_fn, legacy))
            )
            for fn, bucket in ordered:
                started = time.perf_counter()
                fn()
                bucket.append((time.perf_counter() - started) * 1000.0)
    finally:
        if gc_enabled:
            gc.enable()
    return legacy, optimized


def _paired_speedup(legacy: list[float], optimized: list[float]) -> float:
    return statistics.median(
        legacy_ms / max(optimized_ms, 1e-9)
        for legacy_ms, optimized_ms in zip(legacy, optimized, strict=True)
    )


def _fixture(
    *,
    items: int,
    background_users: int,
    evaluated_users: int,
    history: int,
) -> tuple[Catalog, RecommendationEngine, list[str]]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Seed Graph Item {index}",
            text="recommend relevance seed temporal graph benchmark",
            categories=[f"cat-{index % 31}", f"cluster-{index % 67}"],
            popularity=float((items - index) % 997),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0

    def add_user(user_id: str, seed: int) -> None:
        nonlocal timestamp
        for offset in range(history):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[(seed * history + offset) % items].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0

    for user_index in range(background_users):
        add_user(f"bg-{user_index:05d}", user_index)

    evaluated = [f"zz-eval-{index:03d}" for index in range(evaluated_users)]
    for index, user_id in enumerate(evaluated, start=background_users):
        add_user(user_id, index)

    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog), evaluated


def run_benchmark(
    *,
    items: int,
    background_users: int,
    evaluated_users: int,
    history: int,
    repeats: int,
) -> dict[str, object]:
    catalog, engine, evaluated = _fixture(
        items=items,
        background_users=background_users,
        evaluated_users=evaluated_users,
        history=history,
    )
    optimized_builder = validation._owned_temporal_states

    def full_graph_builder(
        current: Catalog,
        target_timestamps: list[float],
        *,
        seed_item_ids=None,
    ):
        del seed_item_ids
        return optimized_builder(current, target_timestamps)

    def legacy_prepare():
        validation._owned_temporal_states = full_graph_builder
        try:
            return validation.prepare_recommend_relevance(
                catalog,
                engine,
                users_override=evaluated,
                k=8,
            )
        finally:
            validation._owned_temporal_states = optimized_builder

    def optimized_prepare():
        validation._owned_temporal_states = optimized_builder
        return validation.prepare_recommend_relevance(
            catalog,
            engine,
            users_override=evaluated,
            k=8,
        )

    legacy = legacy_prepare()
    optimized = optimized_prepare()
    optimized_report = optimized.evaluate(engine.config)
    legacy_report = legacy.evaluate(engine.config)
    if optimized_report != legacy_report:
        raise AssertionError("seed graph snapshots changed relevance evaluation")

    lazy_graphs = [
        row.engine._co
        for row in optimized.slices
        if isinstance(row.engine._co, TemporalGraphSnapshot)
    ]
    if len(lazy_graphs) != len(optimized.slices):
        raise AssertionError("owned multi-slice preparation did not use seed graph snapshots")
    if any(graph.materialized for graph in lazy_graphs):
        raise AssertionError("built-in relevance evaluation materialized a full temporal graph")

    legacy_prepare()
    optimized_prepare()
    legacy_samples, optimized_samples = _paired_timed(
        legacy_prepare,
        optimized_prepare,
        repeats,
    )
    legacy_summary = _summary(legacy_samples)
    optimized_summary = _summary(optimized_samples)
    speedup = _paired_speedup(legacy_samples, optimized_samples)
    return {
        "items": items,
        "interactions": len(catalog.interactions),
        "background_users": background_users,
        "evaluated_users": evaluated_users,
        "history": history,
        "repeats": repeats,
        "prepared_slices": len(optimized.slices),
        "legacy_prepare": legacy_summary,
        "optimized_prepare": optimized_summary,
        "speedup_paired_median": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark seed-only temporal relevance graph snapshots."
    )
    parser.add_argument("--items", type=int, default=20_000)
    parser.add_argument("--background-users", type=int, default=500)
    parser.add_argument("--evaluated-users", type=int, default=10)
    parser.add_argument("--history", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        background_users=max(10, args.background_users),
        evaluated_users=max(2, args.evaluated_users),
        history=max(3, args.history),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized_prepare"]["p50_ms"])
    speedup = float(result["speedup_paired_median"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(
            f"optimized p50={optimized}ms > {args.max_optimized_p50_ms}ms"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "recommend relevance seed graph performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
