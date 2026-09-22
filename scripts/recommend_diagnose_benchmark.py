from __future__ import annotations

import argparse
from collections import defaultdict
import json
import math
import statistics
import time
from types import SimpleNamespace
from typing import Callable

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.runtime.tools_core import ToolRegistry


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
    values: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(calls_per_sample):
            fn()
        values.append(
            (time.perf_counter() - started) * 1_000_000.0 / calls_per_sample
        )
    return values


def _registry(*, items: int, history: int) -> ToolRegistry:
    catalog = Catalog(
        items=[
            Item(
                item_id=f"item-{index:06d}",
                title=f"Item {index}",
                categories=[f"cat-{index % 8}"],
                eligible=index % 11 != 0,
            )
            for index in range(items)
        ],
        interactions=[
            Interaction(
                user_id="warm-user",
                item_id=f"item-{(index * 13) % items:06d}",
                timestamp=float(index),
            )
            for index in range(history)
        ],
    )
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for row in catalog.interactions:
        by_user[row.user_id].append(row)
    registry = object.__new__(ToolRegistry)
    registry.catalog = catalog
    registry._catalog_inspection = ToolRegistry._build_catalog_inspection(catalog)
    registry.recommend = SimpleNamespace(_by_user=by_user)
    return registry


def _legacy_recommend_diagnose(registry: ToolRegistry, user_id: str) -> dict:
    user_id = user_id or "new-user"
    events = registry.recommend._by_user.get(user_id, [])
    seen = {event.item_id for event in events}
    eligible = [
        item
        for item in registry.catalog.items
        if item.eligible and item.item_id not in seen
    ]
    categories = sorted(
        {
            category
            for event in events
            for category in registry.catalog.item_by_id[event.item_id].categories
        }
    )
    return {
        "user_id": user_id,
        "history_events": len(events),
        "seen_items": len(seen),
        "eligible_unseen": len(eligible),
        "known_categories": categories[:12],
        "cold_start": len(events) == 0,
        "diagnosis": (
            "这是冷启动用户，当前结果主要依赖内容质量、新鲜度和稳定探索"
            if not events
            else "可展示未看内容不足，推荐空间受到候选池限制"
            if len(eligible) < 8
            else "用户行为和可展示候选都足以支持个性化复核"
        ),
    }


def _measure(
    registry: ToolRegistry,
    user_id: str,
    *,
    repeats: int,
    calls_per_sample: int,
) -> dict[str, object]:
    legacy_value = _legacy_recommend_diagnose(registry, user_id)
    optimized_value = registry.recommend_diagnose(user_id)
    if legacy_value != optimized_value:
        raise AssertionError(f"recommend diagnosis changed for {user_id}")

    legacy = _summary(
        _timed_per_call(
            lambda: _legacy_recommend_diagnose(registry, user_id),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    optimized = _summary(
        _timed_per_call(
            lambda: registry.recommend_diagnose(user_id),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    speedup = float(legacy["p50_us"]) / max(float(optimized["p50_us"]), 1e-9)
    return {
        "legacy": legacy,
        "optimized": optimized,
        "speedup_p50": round(speedup, 2),
        "eligible_unseen": legacy_value["eligible_unseen"],
    }


def run_benchmark(
    *,
    items: int,
    history: int,
    repeats: int,
    calls_per_sample: int,
) -> dict[str, object]:
    registry = _registry(items=items, history=history)
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "calls_per_sample": calls_per_sample,
        "cold": _measure(
            registry,
            "cold-user",
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        ),
        "warm": _measure(
            registry,
            "warm-user",
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark recommendation diagnosis catalog scans."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=64)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--calls-per-sample", type=int, default=20)
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
            "recommend diagnose performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
