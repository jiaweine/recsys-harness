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
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_ms": round(statistics.fmean(values), 3) if values else 0.0,
        "p50_ms": round(_percentile(values, 0.50), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
        "max_ms": round(max(values), 3) if values else 0.0,
    }


def _timed(fn: Callable[[], object], repeats: int) -> tuple[list[float], object]:
    samples: list[float] = []
    result: object = None
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - started) * 1000.0)
    return samples, result


def _catalog(size: int) -> Catalog:
    items = [
        Item(
            item_id=f"item-{index}",
            title=f"Common Item {index}",
            text=f"common shared catalog item {index}",
            categories=["common", f"group-{index % 32}"],
            popularity=float((index * 7919) % 100_000),
            quality=0.65 + (index % 20) / 100.0,
            freshness=0.60 + (index % 25) / 100.0,
        )
        for index in range(size)
    ]
    return Catalog(items=items, name="popularity-hotpath-benchmark")


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(items)

    popularity_samples, popularity_rows = _timed(
        lambda: [catalog.popularity_norm(item) for item in catalog.items],
        repeats,
    )
    popularity_batch_samples, popularity_batch = _timed(
        catalog.popularity_norms,
        repeats,
    )
    expected_batch = {
        item.item_id: value
        for item, value in zip(catalog.items, popularity_rows, strict=True)
    }
    if popularity_batch != expected_batch:
        raise AssertionError("batch popularity normalization changed values")

    search_init_samples, search = _timed(
        lambda: SearchEngine(catalog),
        repeats,
    )
    assert isinstance(search, SearchEngine)

    prepare_samples, prepared = _timed(
        lambda: search.prepare("common"),
        repeats,
    )
    if not isinstance(prepared, list) or len(prepared) != items:
        raise AssertionError(
            f"expected {items} common search candidates, got "
            f"{len(prepared) if isinstance(prepared, list) else type(prepared)!r}"
        )

    recommend_init_samples, recommend = _timed(
        lambda: RecommendationEngine(catalog),
        repeats,
    )
    assert isinstance(recommend, RecommendationEngine)

    expected = [catalog.popularity_norm(item) for item in catalog.items[:32]]
    observed_search = [
        row["pop"]
        for row in prepared[:32]
    ]
    observed_recommend = [
        recommend._popularity[item.item_id]  # noqa: SLF001 - benchmark contract
        for item in catalog.items[:32]
    ]
    if observed_search != expected or observed_recommend != expected:
        raise AssertionError("engine popularity normalization changed")

    return {
        "items": items,
        "repeats": repeats,
        "popularity_full_pass": _summary(popularity_samples),
        "popularity_batch": _summary(popularity_batch_samples),
        "search_engine_init": _summary(search_init_samples),
        "search_prepare_common": _summary(prepare_samples),
        "recommend_engine_init": _summary(recommend_init_samples),
        "prepared_candidates": len(prepared),
        "popularity_rows": len(popularity_rows) if isinstance(popularity_rows, list) else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark repeated catalog popularity normalization in search/recommend."
    )
    parser.add_argument("--items", type=int, default=6000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-batch-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-search-prepare-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-recommend-init-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-normalization-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1000, args.items),
        repeats=max(2, args.repeats),
    )
    direct_p50 = float(result["popularity_full_pass"]["p50_ms"])
    batch_p50 = float(result["popularity_batch"]["p50_ms"])
    speedup = direct_p50 / max(batch_p50, 1e-9)
    result["normalization_speedup_p50"] = round(speedup, 2)
    print(json.dumps(result, sort_keys=True))

    failures: list[str] = []
    if args.max_batch_p50_ms > 0 and batch_p50 > args.max_batch_p50_ms:
        failures.append(
            f"batch popularity p50={batch_p50} > {args.max_batch_p50_ms}"
        )
    search_prepare_p50 = float(result["search_prepare_common"]["p50_ms"])
    if (
        args.max_search_prepare_p50_ms > 0
        and search_prepare_p50 > args.max_search_prepare_p50_ms
    ):
        failures.append(
            f"search prepare p50={search_prepare_p50} > {args.max_search_prepare_p50_ms}"
        )
    recommend_init_p50 = float(result["recommend_engine_init"]["p50_ms"])
    if (
        args.max_recommend_init_p50_ms > 0
        and recommend_init_p50 > args.max_recommend_init_p50_ms
    ):
        failures.append(
            f"recommend init p50={recommend_init_p50} > {args.max_recommend_init_p50_ms}"
        )
    if (
        args.min_normalization_speedup > 0
        and speedup < args.min_normalization_speedup
    ):
        failures.append(
            f"normalization speedup={speedup:.2f} < {args.min_normalization_speedup}"
        )
    if failures:
        raise SystemExit(
            "popularity hot-path performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
