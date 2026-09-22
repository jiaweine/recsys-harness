from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.text import cosine
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
    out = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _engine(*, items: int) -> SearchEngine:
    rows = [
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
    ]
    return SearchEngine(Catalog(items=rows))


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    engine = _engine(items=items)
    query = "wireless headphones"
    original = search_module._query_cosine

    search_module._query_cosine = (
        lambda qvec, qitems, item_vector: cosine(qvec, item_vector)
    )
    try:
        legacy_prepared = engine.prepare(query)
        legacy_results = engine.search(query, limit=8)
        legacy_prepare = _summary(
            _timed(lambda: engine.prepare(query), repeats=repeats)
        )
        legacy_search = _summary(
            _timed(lambda: engine.search(query, limit=8), repeats=repeats)
        )
    finally:
        search_module._query_cosine = original

    optimized_prepared = engine.prepare(query)
    optimized_results = engine.search(query, limit=8)
    if optimized_prepared != legacy_prepared:
        raise AssertionError("query-vector reuse changed prepared search rows")
    if optimized_results != legacy_results:
        raise AssertionError("query-vector reuse changed search results")

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
        description="Benchmark reused search query-vector iteration."
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
    optimized = float(result["optimized_search"]["p50_ms"])
    prepare_speedup = float(result["prepare_speedup_p50"])
    search_speedup = float(result["search_speedup_p50"])
    if (
        args.max_optimized_search_p50_ms > 0
        and optimized > args.max_optimized_search_p50_ms
    ):
        failures.append(
            f"optimized search p50={optimized}ms > "
            f"{args.max_optimized_search_p50_ms}ms"
        )
    if args.min_prepare_speedup > 0 and prepare_speedup < args.min_prepare_speedup:
        failures.append(
            f"prepare speedup={prepare_speedup} < {args.min_prepare_speedup}"
        )
    if args.min_search_speedup > 0 and search_speedup < args.min_search_speedup:
        failures.append(
            f"search speedup={search_speedup} < {args.min_search_speedup}"
        )
    if failures:
        raise SystemExit(
            "search query-vector performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
