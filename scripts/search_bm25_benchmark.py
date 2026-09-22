from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import SearchEngine
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
    out: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _catalog(items: int) -> Catalog:
    return Catalog(
        items=[
            Item(
                item_id=f"item-{index:06d}",
                title=f"Wireless Headphones {index}",
                text="wireless bluetooth audio headphones travel music",
                categories=["audio", "headphones", f"series-{index % 12}"],
                popularity=float(items - index),
                quality=0.55 + 0.4 * ((index % 17) / 16.0),
                freshness=0.45 + 0.5 * ((index % 19) / 18.0),
            )
            for index in range(items)
        ],
        name="search-bm25-benchmark",
    )


def _legacy_bm25(
    engine: SearchEngine,
    item: Item,
    qtokens: list[str],
    query_weights: dict[str, float] | None = None,
) -> float:
    del query_weights
    toks = engine._doc_tokens[item.item_id]
    title_tokens, text_tokens, category_tokens = engine._field_tokens[item.item_id]
    title_tf = Counter(title_tokens)
    text_tf = Counter(text_tokens)
    category_tf = Counter(category_tokens)
    dl = max(1, len(toks))
    score = 0.0
    k1, b = 1.45, 0.72
    for token in qtokens:
        f = (
            2.1 * title_tf.get(token, 0)
            + text_tf.get(token, 0)
            + 0.75 * category_tf.get(token, 0)
        )
        if f <= 0:
            continue
        query_weight = 0.45 if token in engine.GENERIC_QUERY_TOKENS else 1.0
        score += query_weight * engine._idf(token) * (f * (k1 + 1)) / (
            f + k1 * (1 - b + b * dl / max(1.0, engine._avg_len))
        )
    return score


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    query = "wireless headphones"
    engine = SearchEngine(_catalog(items))
    original = SearchEngine._bm25

    SearchEngine._bm25 = _legacy_bm25
    try:
        legacy_prepared = engine.prepare(query)
        legacy_results = engine.search(query, limit=8)
        engine.prepare(query)
        engine.search(query, limit=8)
        legacy_prepare = _summary(
            _timed(lambda: engine.prepare(query), repeats=repeats)
        )
        legacy_search = _summary(
            _timed(lambda: engine.search(query, limit=8), repeats=repeats)
        )
    finally:
        SearchEngine._bm25 = original

    optimized_prepared = engine.prepare(query)
    optimized_results = engine.search(query, limit=8)
    if optimized_prepared != legacy_prepared:
        raise AssertionError("cached BM25 changed prepared search rows")
    if optimized_results != legacy_results:
        raise AssertionError("cached BM25 changed search results")

    engine.prepare(query)
    engine.search(query, limit=8)
    optimized_prepare = _summary(
        _timed(lambda: engine.prepare(query), repeats=repeats)
    )
    optimized_search = _summary(
        _timed(lambda: engine.search(query, limit=8), repeats=repeats)
    )

    prepare_speedup = float(legacy_prepare["p50_ms"]) / max(
        float(optimized_prepare["p50_ms"]),
        1e-9,
    )
    search_speedup = float(legacy_search["p50_ms"]) / max(
        float(optimized_search["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "repeats": repeats,
        "legacy_prepare": legacy_prepare,
        "optimized_prepare": optimized_prepare,
        "prepare_speedup_p50": round(prepare_speedup, 2),
        "legacy_search": legacy_search,
        "optimized_search": optimized_search,
        "search_speedup_p50": round(search_speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark cached static BM25 search statistics."
    )
    parser.add_argument("--items", type=int, default=6_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-search-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-search-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    search_p50 = float(result["optimized_search"]["p50_ms"])
    prepare_speedup = float(result["prepare_speedup_p50"])
    search_speedup = float(result["search_speedup_p50"])
    if (
        args.max_optimized_search_p50_ms > 0
        and search_p50 > args.max_optimized_search_p50_ms
    ):
        failures.append(
            f"optimized search p50={search_p50}ms > "
            f"{args.max_optimized_search_p50_ms}ms"
        )
    if (
        args.min_prepare_speedup > 0
        and prepare_speedup < args.min_prepare_speedup
    ):
        failures.append(
            f"prepare speedup={prepare_speedup} < {args.min_prepare_speedup}"
        )
    if args.min_search_speedup > 0 and search_speedup < args.min_search_speedup:
        failures.append(
            f"search speedup={search_speedup} < {args.min_search_speedup}"
        )
    if failures:
        raise SystemExit(
            "search BM25 performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
