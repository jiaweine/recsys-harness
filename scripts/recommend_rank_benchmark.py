from __future__ import annotations

import argparse
from heapq import nsmallest
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES, normalize_strategy_config
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.serving import normalize_serving_limit


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


def _engine_and_prepared(*, items: int) -> tuple[RecommendationEngine, list[dict]]:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 32}", f"cat-{(index * 7) % 32}"],
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 256: 0.8,
            (index * 11 + 3) % 256: 0.6,
            (index * 17 + 5) % 256: 0.4,
        }
        for index, item in enumerate(catalog_items)
    }
    engine = RecommendationEngine(Catalog(items=catalog_items), item_vectors=vectors)

    prepared: list[dict] = []
    for index, item in enumerate(catalog_items):
        popularity = ((index * 17) % 1000) / 1000.0
        prepared.append(
            {
                "item": item,
                "profile_fit": ((index * 31) % 1000) / 1000.0,
                "cat_fit": ((index * 41) % 1000) / 1000.0,
                "graph": ((index * 37) % 1000) / 1000.0,
                "pop": popularity,
                "novelty": 1.0 - popularity,
                "explore": ((index * 43) % 1000) / 1000.0,
                "cold_prior": 0.0,
            }
        )
    return engine, prepared


def _legacy_rank_prepared(
    engine: RecommendationEngine,
    prepared: list[dict],
    *,
    limit: int,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    cfg = normalize_strategy_config(engine.config)
    rows = []
    for raw in prepared:
        item = raw["item"]
        base = (
            cfg.profile * raw["profile_fit"]
            + cfg.graph * raw["graph"]
            + cfg.category * raw["cat_fit"]
            + cfg.quality * item.quality
            + cfg.freshness * item.freshness
            + cfg.popularity * raw["pop"]
            + cfg.novelty * raw["novelty"]
            + cfg.exploration * raw["explore"]
            + cfg.cold_start * raw.get("cold_prior", 0.0)
        )
        rows.append(
            {
                "item": item,
                "base": base,
                "signals": {
                    "fit": round(
                        min(
                            1.0,
                            0.55 * raw["profile_fit"]
                            + 0.30 * raw["cat_fit"]
                            + 0.15 * raw["graph"],
                        ),
                        4,
                    ),
                    "quality": round(item.quality, 4),
                    "freshness": round(item.freshness, 4),
                    "novelty": round(raw["novelty"], 4),
                },
            }
        )
    pool = nsmallest(
        max(40, limit * 6),
        rows,
        key=lambda row: (-row["base"], row["item"].item_id),
    )
    selected = []
    while pool and len(selected) < limit:
        best = None
        best_score = float("-inf")
        for row in pool:
            redundancy = max(
                (
                    CAPABILITIES.call(
                        "recommend.rerank",
                        cfg.rerank_strategy,
                        engine,
                        row["item"],
                        chosen["item"],
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            adjusted = row["base"] - cfg.diversity * redundancy
            if adjusted > best_score:
                best_score, best = adjusted, row
        assert best is not None
        selected.append({**best, "adjusted": best_score})
        pool.remove(best)
    return [
        {
            "rank": index + 1,
            **row["item"].public_dict(),
            "score": round(row["adjusted"], 5),
            "signals": row["signals"],
        }
        for index, row in enumerate(selected)
    ]


def run_benchmark(*, items: int, repeats: int, limit: int) -> dict[str, object]:
    engine, prepared = _engine_and_prepared(items=items)

    legacy_result = _legacy_rank_prepared(engine, prepared, limit=limit)
    optimized_result = engine.rank_prepared(prepared, limit=limit)
    if optimized_result != legacy_result:
        raise AssertionError(
            "optimized recommendation ranking differs from pre-deferred-signals output"
        )

    legacy_stats = _summary(
        _timed(
            lambda: _legacy_rank_prepared(engine, prepared, limit=limit),
            repeats=repeats,
        )
    )
    optimized_stats = _summary(
        _timed(
            lambda: engine.rank_prepared(prepared, limit=limit),
            repeats=repeats,
        )
    )
    speedup = float(legacy_stats["p50_ms"]) / max(
        float(optimized_stats["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "repeats": repeats,
        "limit": limit,
        "mmr_pool": max(40, limit * 6),
        "legacy": legacy_stats,
        "optimized": optimized_stats,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark deferred recommendation rank signal construction."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        repeats=max(3, args.repeats),
        limit=max(1, args.limit),
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
            "recommend rank performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
