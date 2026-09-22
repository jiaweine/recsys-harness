from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.item_features import ItemVectorSnapshot
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


def _fixture(*, items: int, history: int) -> tuple[RecommendationEngine, str]:
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
    vectors = ItemVectorSnapshot(
        {
            item.item_id: {
                (index * 7 + offset * 13) % 256: (offset + 1) / 64.0
                for offset in range(48)
            }
            for index, item in enumerate(rows)
        },
        dense_dims=256,
    )
    user_id = "warm-user"
    interactions = [
        Interaction(
            user_id=user_id,
            item_id=rows[index].item_id,
            event="click",
            weight=1.0 + (index % 5) * 0.1,
            timestamp=float(index + 1),
        )
        for index in range(history)
    ]
    engine = RecommendationEngine(
        Catalog(items=rows, interactions=interactions),
        item_vectors=vectors,
    )
    return engine, user_id


def run_benchmark(
    *,
    items: int,
    history: int,
    repeats: int,
    limit: int,
) -> dict[str, object]:
    engine, user_id = _fixture(items=items, history=history)
    dims = engine._dense_vector_dims

    engine._dense_vector_dims = None
    legacy_prepared = engine.prepare(user_id)
    legacy_results = engine.recommend(user_id, limit=limit)
    legacy_prepare = _summary(
        _timed(lambda: engine.prepare(user_id), repeats=repeats)
    )
    legacy_recommend = _summary(
        _timed(lambda: engine.recommend(user_id, limit=limit), repeats=repeats)
    )

    engine._dense_vector_dims = dims
    optimized_prepared = engine.prepare(user_id)
    optimized_results = engine.recommend(user_id, limit=limit)
    if optimized_prepared != legacy_prepared:
        raise AssertionError("dense profile lookup changed prepared rows")
    if optimized_results != legacy_results:
        raise AssertionError("dense profile lookup changed recommendation output")

    optimized_prepare = _summary(
        _timed(lambda: engine.prepare(user_id), repeats=repeats)
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
        "history": history,
        "limit": limit,
        "repeats": repeats,
        "legacy_prepare": legacy_prepare,
        "optimized_prepare": optimized_prepare,
        "prepare_speedup_p50": round(prepare_speedup, 2),
        "legacy_recommend": legacy_recommend,
        "optimized_recommend": optimized_recommend,
        "recommend_speedup_p50": round(recommend_speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark dense warm recommendation profile lookup."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-optimized-recommend-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-recommend-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(2, args.history),
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
            "dense recommendation profile performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
