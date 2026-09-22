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
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    samples: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples


def _engine_and_prepared(*, items: int) -> tuple[SearchEngine, list[dict]]:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Benchmark Item {index}",
            categories=[f"category-{index % 23}", f"cluster-{index % 71}"],
            popularity=float((index * 17) % 1000),
            quality=((index * 13) % 100) / 100.0,
            freshness=((index * 29) % 100) / 100.0,
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
    config: SearchConfig | None = None,
    limit: int = 10,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    cfg = config or engine.config
    rows: list[dict] = []
    for raw in prepared:
        item = raw["item"]
        base = (
            cfg.lexical * raw["lex"]
            + cfg.semantic * raw["sem"]
            + cfg.title * raw["title"]
            + cfg.quality * item.quality
            + cfg.popularity * raw["pop"]
            + cfg.freshness * item.freshness
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
    configs = [
        ("category_mmr", replace(engine.config, rerank_strategy="category_mmr")),
        ("semantic_mmr", replace(engine.config, rerank_strategy="semantic_mmr")),
        ("hybrid_mmr", replace(engine.config, rerank_strategy="hybrid_mmr")),
    ]

    results: dict[str, object] = {}
    for name, config in configs:
        legacy_result = _legacy_rank_prepared(
            engine,
            prepared,
            config=config,
            limit=limit,
        )
        current_result = engine.rank_prepared(
            prepared,
            config=config,
            limit=limit,
        )
        if current_result != legacy_result:
            raise AssertionError(f"search rank output differs for {name}")

        results[name] = {
            "legacy": _summary(
                _timed(
                    lambda config=config: _legacy_rank_prepared(
                        engine,
                        prepared,
                        config=config,
                        limit=limit,
                    ),
                    repeats,
                )
            ),
            "current": _summary(
                _timed(
                    lambda config=config: engine.rank_prepared(
                        prepared,
                        config=config,
                        limit=limit,
                    ),
                    repeats,
                )
            ),
        }
    return {
        "items": items,
        "limit": limit,
        "pool_size": max(30, limit * 6),
        "rerank": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark search ranking row/signal materialization."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-current-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        items=max(1_000, args.items),
        repeats=max(3, args.repeats),
        limit=max(1, args.limit),
    )

    failures: list[str] = []
    for name, stats in result["rerank"].items():
        current_p50 = float(stats["current"]["p50_ms"])
        legacy_p50 = float(stats["legacy"]["p50_ms"])
        speedup = legacy_p50 / max(current_p50, 1e-9)
        stats["speedup_p50"] = round(speedup, 2)
        if args.max_current_p50_ms > 0 and current_p50 > args.max_current_p50_ms:
            failures.append(
                f"{name} current p50={current_p50} > {args.max_current_p50_ms}"
            )
        if args.min_speedup > 0 and speedup < args.min_speedup:
            failures.append(
                f"{name} speedup={speedup:.2f} < {args.min_speedup}"
            )

    print(json.dumps(result, sort_keys=True))
    if failures:
        raise SystemExit("search rank performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
