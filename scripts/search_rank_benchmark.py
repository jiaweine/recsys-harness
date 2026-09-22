from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.search import SearchConfig
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


def _engine_and_prepared(*, items: int) -> tuple[SearchEngine, list[dict]]:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Benchmark Item {index}",
            categories=[f"category-{index % 23}", f"cluster-{index % 71}"],
            popularity=float((index * 17) % 1000),
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
    engine = SearchEngine(Catalog(items=catalog_items), item_vectors=vectors)
    prepared = [
        {
            "item": item,
            "lex_raw": ((index * 37) % 1000) / 1000.0,
            "lex": ((index * 37) % 1000) / 1000.0,
            "sem": ((index * 19 + 7) % 1000) / 1000.0,
            "title": ((index * 23 + 5) % 1000) / 1000.0,
            "pop": engine._popularity[item.item_id],
            "candidate_source": "lexical",
        }
        for index, item in enumerate(catalog_items)
    ]
    return engine, prepared


def _legacy_rank_prepared(
    engine: SearchEngine,
    prepared: list[dict],
    *,
    config: SearchConfig,
    limit: int,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    rows: list[dict] = []
    for raw in prepared:
        item = raw["item"]
        base = (
            config.lexical * raw["lex"]
            + config.semantic * raw["sem"]
            + config.title * raw["title"]
            + config.quality * item.quality
            + config.popularity * raw["pop"]
            + config.freshness * item.freshness
        )
        rows.append(
            {
                **raw,
                "base": base,
                "signals": {
                    "match": round(0.65 * raw["lex"] + 0.35 * raw["sem"], 4),
                    "quality": round(item.quality, 4),
                    "freshness": round(item.freshness, 4),
                    "popularity": round(raw["pop"], 4),
                },
            }
        )
    rows.sort(key=lambda row: (-row["base"], row["item"].item_id))
    pool = rows[: max(30, limit * 6)]
    selected: list[dict] = []
    while pool and len(selected) < limit:
        best = None
        best_score = float("-inf")
        for row in pool:
            redundancy = max(
                (
                    CAPABILITIES.call(
                        "search.rerank",
                        config.rerank_strategy,
                        engine,
                        row["item"],
                        chosen["item"],
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            adjusted = row["base"] - config.diversity * redundancy
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
    results: dict[str, object] = {}

    for rerank_strategy in ("category_mmr", "semantic_mmr", "hybrid_mmr"):
        config = replace(engine.config, rerank_strategy=rerank_strategy)
        legacy_result = _legacy_rank_prepared(
            engine,
            prepared,
            config=config,
            limit=limit,
        )
        optimized_result = engine.rank_prepared(
            prepared,
            config=config,
            limit=limit,
        )
        if optimized_result != legacy_result:
            raise AssertionError(f"search rank output differs for {rerank_strategy}")

        legacy = _summary(
            _timed(
                lambda config=config: _legacy_rank_prepared(
                    engine,
                    prepared,
                    config=config,
                    limit=limit,
                ),
                repeats=repeats,
            )
        )
        optimized = _summary(
            _timed(
                lambda config=config: engine.rank_prepared(
                    prepared,
                    config=config,
                    limit=limit,
                ),
                repeats=repeats,
            )
        )
        speedup = float(legacy["p50_ms"]) / max(float(optimized["p50_ms"]), 1e-9)
        results[rerank_strategy] = {
            "legacy": legacy,
            "optimized": optimized,
            "speedup_p50": round(speedup, 2),
        }

    return {
        "items": items,
        "limit": limit,
        "repeats": repeats,
        "pool_size": max(30, limit * 6),
        "rerank": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark deferred search rank signal construction."
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
    for name, row in result["rerank"].items():
        optimized = float(row["optimized"]["p50_ms"])
        speedup = float(row["speedup_p50"])
        if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
            failures.append(
                f"{name} optimized p50={optimized}ms > {args.max_optimized_p50_ms}ms"
            )
        if args.min_speedup > 0 and speedup < args.min_speedup:
            failures.append(f"{name} speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "search rank performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
