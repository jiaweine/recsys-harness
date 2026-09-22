from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.algorithms.item_features import build_item_vectors


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


def _timed(fn: Callable[[], object], *, repeats: int) -> list[float]:
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _fixture(*, items: int, users: int) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Temporal Item {index}",
            text="recommend relevance temporal validation owned vector snapshot",
            categories=[f"cat-{index % 31}", f"cluster-{index % 67}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(users):
        user_id = f"user-{user_index:02d}"
        for offset in range(5):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[user_index * 8 + offset].item_id,
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
        temporal._vectors = build_item_vectors(training_catalog.items)
        temporal._dense_vector_dims = getattr(temporal._vectors, "dense_dims", None)
        return temporal

    validation._owned_temporal_recommendation_engine = legacy_materializer
    try:
        legacy = validation.prepare_recommend_relevance(
            catalog,
            engine,
            users_override=user_ids,
            k=8,
        )
        legacy_summary = _summary(
            _timed(
                lambda: validation.prepare_recommend_relevance(
                    catalog,
                    engine,
                    users_override=user_ids,
                    k=8,
                ),
                repeats=repeats,
            )
        )
    finally:
        validation._owned_temporal_recommendation_engine = optimized_materializer

    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=user_ids,
        k=8,
    )
    if optimized.evaluate(engine.config) != legacy.evaluate(engine.config):
        raise AssertionError("shared item vectors changed relevance evaluation")

    optimized_summary = _summary(
        _timed(
            lambda: validation.prepare_recommend_relevance(
                catalog,
                engine,
                users_override=user_ids,
                k=8,
            ),
            repeats=repeats,
        )
    )
    speedup = float(legacy_summary["p50_ms"]) / max(
        float(optimized_summary["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "users": users,
        "repeats": repeats,
        "prepared_slices": len(optimized.slices),
        "legacy_prepare": legacy_summary,
        "optimized_prepare": optimized_summary,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark shared temporal relevance item vectors."
    )
    parser.add_argument("--items", type=int, default=4_000)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(500, args.items),
        users=max(3, args.users),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized_prepare"]["p50_ms"])
    speedup = float(result["speedup_p50"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(
            f"optimized p50={optimized}ms > {args.max_optimized_p50_ms}ms"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(
            f"speedup={speedup} < {args.min_speedup}"
        )
    if failures:
        raise SystemExit(
            "recommend relevance vector performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
