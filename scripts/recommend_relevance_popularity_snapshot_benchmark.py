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


def _fixture(*, items: int, users: int) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Temporal Item {index}",
            text="recommend relevance shared popularity snapshot benchmark",
            categories=[f"cat-{index % 31}", f"cluster-{index % 67}"],
            popularity=float((items - index) % 997),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(users):
        for offset in range(6):
            interactions.append(
                Interaction(
                    user_id=f"user-{user_index:02d}",
                    item_id=rows[user_index * 10 + offset].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog)


def run_benchmark(*, items: int, users: int, repeats: int) -> dict[str, object]:
    catalog, engine = _fixture(items=items, users=users)
    user_ids = engine.known_users()
    optimized_materializer = validation._owned_temporal_recommendation_engine

    def legacy_materializer(current, training_catalog, state):
        temporal = optimized_materializer(current, training_catalog, state)
        temporal._popularity = training_catalog.popularity_norms()
        return temporal

    def legacy_prepare():
        validation._owned_temporal_recommendation_engine = legacy_materializer
        try:
            return validation.prepare_recommend_relevance(
                catalog,
                engine,
                users_override=user_ids,
                k=8,
            )
        finally:
            validation._owned_temporal_recommendation_engine = optimized_materializer

    def optimized_prepare():
        validation._owned_temporal_recommendation_engine = optimized_materializer
        return validation.prepare_recommend_relevance(
            catalog,
            engine,
            users_override=user_ids,
            k=8,
        )

    legacy = legacy_prepare()
    optimized = optimized_prepare()
    if optimized.evaluate(engine.config) != legacy.evaluate(engine.config):
        raise AssertionError("shared popularity snapshot changed relevance evaluation")

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
        "users": users,
        "repeats": repeats,
        "prepared_slices": len(optimized.slices),
        "legacy_prepare": legacy_summary,
        "optimized_prepare": optimized_summary,
        "speedup_paired_median": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark shared temporal relevance popularity norms."
    )
    parser.add_argument("--items", type=int, default=20_000)
    parser.add_argument("--users", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        users=max(3, args.users),
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
            "recommend relevance popularity snapshot performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
