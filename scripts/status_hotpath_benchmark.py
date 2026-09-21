from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.production import ExposureEvent, RewardSpec


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


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    values: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        values.append((time.perf_counter() - started) * 1000.0)
    return values


def _catalog(*, items: int, interactions: int, events: int) -> Catalog:
    item_rows = [
        Item(
            item_id=f"item-{index}",
            title=f"Item {index}",
            categories=[f"category-{index % 80}", f"group-{index % 17}"],
            popularity=float(index % 1000),
        )
        for index in range(items)
    ]
    interaction_rows = [
        Interaction(
            user_id=f"user-{index % max(1, items // 4)}",
            item_id=f"item-{index % items}",
            event="click",
            weight=1.0,
            timestamp=float(index),
        )
        for index in range(interactions)
    ]
    exposure_rows = [
        ExposureEvent(
            request_id=f"request-{index // 5}",
            timestamp=float(index),
            surface="search" if index % 2 == 0 else "recommend",
            item_id=f"item-{index % items}",
            event="click",
            query="camp light" if index % 2 == 0 else "",
            user_id="" if index % 2 == 0 else f"user-{index % max(1, items // 4)}",
        )
        for index in range(events)
    ]
    return Catalog(
        items=item_rows,
        interactions=interaction_rows,
        events=exposure_rows,
        reward_spec=RewardSpec(weights={"click": 1.0}),
        name="status-benchmark",
    )


def _cached_api_summary(catalog: Catalog, repeats: int) -> tuple[dict[str, float], float]:
    import lingjing_harness.api_core as api_core

    original_catalog = api_core.catalog
    original_revision = api_core.CATALOG_REVISION
    original_cache = api_core._CATALOG_SUMMARY_CACHE
    try:
        api_core.catalog = catalog
        api_core.CATALOG_REVISION = "status-benchmark-revision"
        api_core._CATALOG_SUMMARY_CACHE = None
        expected = api_core._catalog_summary()
        samples = _timed(api_core._catalog_summary, repeats)

        appended = ExposureEvent(
            request_id="request-appended",
            timestamp=float(len(catalog.events) + 1),
            surface="search",
            item_id=catalog.items[0].item_id,
            event="click",
            query="cache invalidation",
        )
        catalog.events.append(appended)
        started = time.perf_counter()
        refreshed = api_core._catalog_summary()
        invalidation_ms = (time.perf_counter() - started) * 1000.0
        if refreshed["production_events"] != expected["production_events"] + 1:
            raise AssertionError("API summary cache did not invalidate after event append")
        return _summary(samples), round(invalidation_ms, 3)
    finally:
        api_core.catalog = original_catalog
        api_core.CATALOG_REVISION = original_revision
        api_core._CATALOG_SUMMARY_CACHE = original_cache


def run_benchmark(*, items: int, interactions: int, events: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(items=items, interactions=interactions, events=events)
    expected = catalog.summary()
    raw_samples = _timed(catalog.summary, repeats)
    observed = catalog.summary()
    if observed != expected:
        raise AssertionError("catalog summary changed during read-only benchmark")

    cached_samples, invalidation_ms = _cached_api_summary(catalog, repeats * 4)
    return {
        "items": items,
        "interactions": interactions,
        "events": events,
        "summary": expected,
        "catalog_summary_raw": _summary(raw_samples),
        "catalog_summary_cached": cached_samples,
        "cache_invalidation_ms": invalidation_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark the catalog portion of the /api/status hot path."
    )
    parser.add_argument("--items", type=int, default=20_000)
    parser.add_argument("--interactions", type=int, default=200_000)
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    print(
        json.dumps(
            run_benchmark(
                items=max(1000, args.items),
                interactions=max(1000, args.interactions),
                events=max(1000, args.events),
                repeats=max(10, args.repeats),
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
