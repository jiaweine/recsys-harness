from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import RecommendationEngine, SearchEngine
from lingjing_harness.domain import Catalog, Item


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
    out: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _catalog(items: int) -> Catalog:
    return Catalog(
        items=[
            Item(
                item_id=f"item-{index}",
                title=f"Common item {index}",
                text="common benchmark catalog item",
                categories=[f"category-{index % 50}", "common"],
                popularity=float((index * 17) % 10_000),
                quality=0.7,
                freshness=0.8,
            )
            for index in range(items)
        ],
        name="popularity-benchmark",
    )


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(items)
    expected = [
        catalog.popularity_norm(item)
        for item in (catalog.items[0], catalog.items[len(catalog.items) // 2], catalog.items[-1])
    ]

    normalize_all = _timed(
        lambda: [catalog.popularity_norm(item) for item in catalog.items],
        repeats,
    )

    recommend_init = _timed(
        lambda: RecommendationEngine(catalog),
        max(1, min(3, repeats)),
    )

    search = SearchEngine(catalog)
    prepared = search.prepare("common")
    if len(prepared) != items:
        raise AssertionError(f"expected {items} broad-query candidates, got {len(prepared)}")
    search_prepare = _timed(
        lambda: search.prepare("common"),
        repeats,
    )

    observed = [
        catalog.popularity_norm(item)
        for item in (catalog.items[0], catalog.items[len(catalog.items) // 2], catalog.items[-1])
    ]
    if observed != expected:
        raise AssertionError("popularity normalization changed during benchmark")

    return {
        "items": items,
        "normalize_all": _summary(normalize_all),
        "recommend_init": _summary(recommend_init),
        "search_prepare": _summary(search_prepare),
        "sample": [round(value, 8) for value in observed],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark catalog popularity normalization hot paths."
    )
    parser.add_argument("--items", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    print(
        json.dumps(
            run_benchmark(
                items=max(500, args.items),
                repeats=max(2, args.repeats),
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
