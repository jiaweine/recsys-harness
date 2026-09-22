from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import statistics
import time
from types import SimpleNamespace
from typing import Callable

from lingjing_harness.algorithms import SearchConfig, SegmentRouter
from lingjing_harness.domain import Catalog, Interaction, Item


class _SearchStub:
    config = SearchConfig()

    @staticmethod
    def prepare(query: str) -> list[dict]:
        return []


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "count": float(len(values)),
        "mean_us": round(statistics.fmean(values), 3),
        "p50_us": round(_percentile(values, 0.50), 3),
        "p95_us": round(_percentile(values, 0.95), 3),
        "max_us": round(max(values), 3),
    }


def _timed_per_call(
    fn: Callable[[], object],
    *,
    repeats: int,
    calls_per_sample: int,
) -> list[float]:
    out: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(calls_per_sample):
            fn()
        elapsed_us = (time.perf_counter() - started) * 1_000_000.0
        out.append(elapsed_us / calls_per_sample)
    return out


def _router(*, items: int, history: int) -> SegmentRouter:
    catalog_items = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            eligible=index % 11 != 0,
        )
        for index in range(items)
    ]
    interactions = [
        Interaction(
            user_id="warm-user",
            item_id=f"item-{(index * 13) % items:06d}",
            timestamp=float(index),
        )
        for index in range(history)
    ]
    catalog = Catalog(items=catalog_items, interactions=interactions)
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for row in catalog.interactions:
        by_user[row.user_id].append(row)
    recommend = SimpleNamespace(_by_user=by_user)
    return SegmentRouter(catalog, _SearchStub(), recommend)


def _legacy_features(router: SegmentRouter, user_id: str) -> tuple[int, int]:
    events = router.recommend._by_user.get(user_id or "", [])
    seen = {event.item_id for event in events}
    eligible_unseen = sum(
        1
        for item in router.catalog.items
        if item.eligible and item.item_id not in seen
    )
    return len(events), eligible_unseen


def _optimized_features(router: SegmentRouter, user_id: str) -> tuple[int, int]:
    features = router.recommend_features(user_id)
    return features.history_events, features.eligible_unseen


def _measure_user(
    router: SegmentRouter,
    user_id: str,
    *,
    repeats: int,
    calls_per_sample: int,
) -> dict[str, object]:
    legacy_value = _legacy_features(router, user_id)
    optimized_value = _optimized_features(router, user_id)
    if legacy_value != optimized_value:
        raise AssertionError(
            f"routing features changed for {user_id}: {legacy_value} != {optimized_value}"
        )

    legacy = _summary(
        _timed_per_call(
            lambda: _legacy_features(router, user_id),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    optimized = _summary(
        _timed_per_call(
            lambda: _optimized_features(router, user_id),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    speedup = float(legacy["p50_us"]) / max(float(optimized["p50_us"]), 1e-9)
    return {
        "legacy": legacy,
        "optimized": optimized,
        "speedup_p50": round(speedup, 2),
        "features": {"history_events": legacy_value[0], "eligible_unseen": legacy_value[1]},
    }


def run_benchmark(
    *,
    items: int,
    history: int,
    repeats: int,
    calls_per_sample: int,
) -> dict[str, object]:
    router = _router(items=items, history=history)
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "calls_per_sample": calls_per_sample,
        "cold": _measure_user(
            router,
            "cold-user",
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        ),
        "warm": _measure_user(
            router,
            "warm-user",
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark recommendation segment routing item scans."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--calls-per-sample", type=int, default=30)
    parser.add_argument("--max-optimized-p50-us", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(0, args.history),
        repeats=max(3, args.repeats),
        calls_per_sample=max(1, args.calls_per_sample),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    for label in ("cold", "warm"):
        row = result[label]
        optimized = float(row["optimized"]["p50_us"])
        speedup = float(row["speedup_p50"])
        if args.max_optimized_p50_us > 0 and optimized > args.max_optimized_p50_us:
            failures.append(
                f"{label} optimized p50={optimized}us > {args.max_optimized_p50_us}us"
            )
        if args.min_speedup > 0 and speedup < args.min_speedup:
            failures.append(f"{label} speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "recommend routing performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
