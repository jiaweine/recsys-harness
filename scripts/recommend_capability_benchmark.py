from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.domain import Catalog, Item


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


def _engine(*, items: int) -> RecommendationEngine:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 32}", f"cluster-{index % 71}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    return RecommendationEngine(
        Catalog(items=rows),
        item_vectors={item.item_id: {} for item in rows},
    )


def _legacy_cold_prepare(engine: RecommendationEngine, user_id: str) -> list[dict]:
    profile, cats, seen, seeds = engine._profile(user_id)
    cat_total = sum(cats.values()) or 1.0
    graph_scores = engine._graph_scores(seeds)
    candidate_ids = CAPABILITIES.call(
        "recommend.candidate",
        engine.config.candidate_strategy,
        engine,
        user_id,
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )
    rows = []
    for item_id in dict.fromkeys(str(value) for value in candidate_ids):
        item = engine.catalog.item_by_id.get(item_id)
        if item is None or not item.eligible or item.item_id in seen:
            continue
        popularity = engine._popularity[item.item_id]
        explore = CAPABILITIES.call(
            "recommend.exploration",
            engine.config.exploration_strategy,
            engine,
            user_id,
            item,
            popularity,
        )
        cold_prior = CAPABILITIES.call(
            "recommend.cold_start",
            engine.config.cold_start_strategy,
            engine,
            item,
            popularity,
            explore,
        )
        rows.append(
            {
                "item": item,
                "profile_fit": 0.0,
                "cat_fit": 0.0 / cat_total,
                "graph": 0.0,
                "pop": popularity,
                "novelty": 1.0 - popularity,
                "explore": explore,
                "cold_prior": cold_prior,
            }
        )
    return rows


def run_benchmark(*, items: int, repeats: int, limit: int) -> dict[str, object]:
    engine = _engine(items=items)
    user_id = "new-user"

    legacy_prepared = _legacy_cold_prepare(engine, user_id)
    optimized_prepared = engine.prepare(user_id)
    if optimized_prepared != legacy_prepared:
        raise AssertionError("bound handlers changed cold prepared rows")

    legacy_results = engine.rank_prepared(legacy_prepared, limit=limit)
    optimized_results = engine.recommend(user_id, limit=limit)
    if optimized_results != legacy_results:
        raise AssertionError("bound handlers changed cold recommendation output")

    legacy_prepare = _summary(
        _timed(
            lambda: _legacy_cold_prepare(engine, user_id),
            repeats=repeats,
        )
    )
    optimized_prepare = _summary(
        _timed(lambda: engine.prepare(user_id), repeats=repeats)
    )
    legacy_recommend = _summary(
        _timed(
            lambda: engine.rank_prepared(
                _legacy_cold_prepare(engine, user_id),
                limit=limit,
            ),
            repeats=repeats,
        )
    )
    optimized_recommend = _summary(
        _timed(lambda: engine.recommend(user_id, limit=limit), repeats=repeats)
    )

    prepare_speedup = float(legacy_prepare["p50_ms"]) / max(
        float(optimized_prepare["p50_ms"]),
        1e-9,
    )
    recommend_speedup = float(legacy_recommend["p50_ms"]) / max(
        float(optimized_recommend["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "repeats": repeats,
        "limit": limit,
        "legacy_prepare": legacy_prepare,
        "optimized_prepare": optimized_prepare,
        "prepare_speedup_p50": round(prepare_speedup, 2),
        "legacy_recommend": legacy_recommend,
        "optimized_recommend": optimized_recommend,
        "recommend_speedup_p50": round(recommend_speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark per-request recommendation capability binding."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-optimized-recommend-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-recommend-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        repeats=max(3, args.repeats),
        limit=max(1, args.limit),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    optimized = float(result["optimized_recommend"]["p50_ms"])
    prepare_speedup = float(result["prepare_speedup_p50"])
    recommend_speedup = float(result["recommend_speedup_p50"])
    if (
        args.max_optimized_recommend_p50_ms > 0
        and optimized > args.max_optimized_recommend_p50_ms
    ):
        failures.append(
            f"optimized recommend p50={optimized}ms > "
            f"{args.max_optimized_recommend_p50_ms}ms"
        )
    if args.min_prepare_speedup > 0 and prepare_speedup < args.min_prepare_speedup:
        failures.append(
            f"prepare speedup={prepare_speedup} < {args.min_prepare_speedup}"
        )
    if (
        args.min_recommend_speedup > 0
        and recommend_speedup < args.min_recommend_speedup
    ):
        failures.append(
            f"recommend speedup={recommend_speedup} < {args.min_recommend_speedup}"
        )
    if failures:
        raise SystemExit(
            "recommend capability performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
