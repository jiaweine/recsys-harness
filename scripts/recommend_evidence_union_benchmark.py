from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms.recommend_core import (
    RecommendConfig,
    RecommendationEngine,
    _candidate_evidence_union,
)
from lingjing_harness.algorithms.text import cosine
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


def _timed(fn: Callable[[], object], *, repeats: int) -> list[float]:
    out: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _engine(*, items: int, history: int) -> RecommendationEngine:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 20}", f"cat-{(index * 7) % 20}"],
            popularity=float((index * 17) % 1000),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
            eligible=index % 11 != 0,
        )
        for index in range(items)
    ]
    interactions = [
        Interaction(
            user_id="warm-user",
            item_id=f"item-{(index * 13) % items:06d}",
            timestamp=float(index),
        )
        for index in range(history)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 256: 0.8,
            (index * 11 + 3) % 256: 0.6,
            (index * 17 + 5) % 256: 0.4,
        }
        for index, item in enumerate(catalog_items)
    }
    return RecommendationEngine(
        Catalog(items=catalog_items, interactions=interactions),
        RecommendConfig(candidate_strategy="evidence_union"),
        item_vectors=vectors,
    )


def _legacy_candidate_evidence_union(
    engine: RecommendationEngine,
    user_id: str,
    profile: dict[int, float],
    cats: Counter[str],
    seen: set[str],
    seeds: Counter[str],
    graph_scores: dict[str, float],
) -> list[str]:
    eligible = [
        item
        for item in engine.catalog.items
        if item.eligible and item.item_id not in seen
    ]
    if not seeds or not eligible:
        return [item.item_id for item in eligible]

    selected: set[str] = set(graph_scores)
    if cats:
        category_keys = set(cats)
        for item in eligible:
            if set(item.categories) & category_keys:
                selected.add(item.item_id)

    semantic = []
    if profile:
        for item in eligible:
            semantic.append(
                (max(0.0, cosine(profile, engine._vectors[item.item_id])), item.item_id)
            )
        semantic.sort(key=lambda row: (-row[0], row[1]))
        selected.update(item_id for _, item_id in semantic[:24])

    target = min(len(eligible), max(24, int(len(eligible) * 0.55)))
    if len(selected) < target:
        fallback = sorted(
            eligible,
            key=lambda item: (
                -(
                    0.45 * item.quality
                    + 0.35 * item.freshness
                    + 0.20 * engine._popularity[item.item_id]
                ),
                item.item_id,
            ),
        )
        for item in fallback:
            selected.add(item.item_id)
            if len(selected) >= target:
                break
    return sorted(selected)


def run_benchmark(*, items: int, history: int, repeats: int) -> dict[str, object]:
    engine = _engine(items=items, history=history)
    user_id = "warm-user"
    profile, cats, seen, seeds = engine._profile(user_id)
    graph_scores = engine._graph_scores(seeds)

    def legacy() -> list[str]:
        return _legacy_candidate_evidence_union(
            engine,
            user_id,
            profile,
            cats,
            seen,
            seeds,
            graph_scores,
        )

    def optimized() -> list[str]:
        return _candidate_evidence_union(
            engine,
            user_id,
            profile,
            cats,
            seen,
            seeds,
            graph_scores,
        )

    legacy_result = legacy()
    engine._candidate_static_cache.clear()
    cold_started = time.perf_counter()
    optimized_result = optimized()
    cold_ms = (time.perf_counter() - cold_started) * 1000.0
    if optimized_result != legacy_result:
        raise AssertionError("optimized evidence_union candidates differ from legacy output")

    legacy_stats = _summary(_timed(legacy, repeats=repeats))
    optimized_stats = _summary(_timed(optimized, repeats=repeats))
    speedup = float(legacy_stats["p50_ms"]) / max(
        float(optimized_stats["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "candidate_count": len(legacy_result),
        "cold_optimized_ms": round(cold_ms, 3),
        "legacy": legacy_stats,
        "optimized": optimized_stats,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark evidence_union candidate selection without repeated full sorts."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(1, args.history),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    optimized = float(result["optimized"]["p50_ms"])
    speedup = float(result["speedup_p50"])
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(
            f"optimized p50={optimized}ms > {args.max_optimized_p50_ms}ms"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "evidence union performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
