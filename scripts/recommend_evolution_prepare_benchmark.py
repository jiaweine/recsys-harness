from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
import statistics
import time
from typing import Callable

import lingjing_harness.algorithms.evolution_core as core
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


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


def _fixture(*, items: int, users: int) -> tuple[Catalog, RecommendationEngine, list[str]]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            text="recommend evolution prepare cache benchmark",
            categories=[f"cat-{index % 31}", f"cluster-{index % 67}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    user_ids = [f"user-{index:02d}" for index in range(users)]
    for user_index, user_id in enumerate(user_ids):
        for offset in range(48):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[(user_index * 97 + offset) % items].item_id,
                    weight=1.0 + (offset % 5) * 0.1,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog), user_ids


def _configs(engine: RecommendationEngine, count: int):
    base = engine.config
    variants = [
        base,
        replace(base, diversity=0.18),
        replace(base, profile=0.30, graph=0.24),
        replace(base, quality=0.16, freshness=0.09),
        replace(base, popularity=0.08, novelty=0.03),
        replace(base, exploration=0.08, diversity=0.10),
        replace(base, rerank_strategy="semantic_mmr"),
        replace(base, rerank_strategy="hybrid_mmr"),
    ]
    out = []
    while len(out) < count:
        out.extend(variants)
    return out[:count]


def run_benchmark(
    *,
    items: int,
    users: int,
    configs: int,
    repeats: int,
) -> dict[str, object]:
    catalog, engine, user_ids = _fixture(items=items, users=users)
    variants = _configs(engine, configs)

    def legacy():
        return [
            core._audit_recommend_config(
                catalog,
                engine,
                user_ids,
                config,
                slice_key="discovery",
            )
            for config in variants
        ]

    def optimized():
        prepared_cache: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
        return [
            core._audit_recommend_config(
                catalog,
                engine,
                user_ids,
                config,
                slice_key="discovery",
                prepared_cache=prepared_cache,
            )
            for config in variants
        ]

    legacy_result = legacy()
    optimized_result = optimized()
    if optimized_result != legacy_result:
        raise AssertionError("prepare caching changed recommendation audit output")

    legacy_summary = _summary(_timed(legacy, repeats=repeats))
    optimized_summary = _summary(_timed(optimized, repeats=repeats))
    speedup = float(legacy_summary["p50_ms"]) / max(
        float(optimized_summary["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "users": users,
        "configs": configs,
        "repeats": repeats,
        "legacy": legacy_summary,
        "optimized": optimized_summary,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark recommendation evolution prepare reuse."
    )
    parser.add_argument("--items", type=int, default=6_000)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--configs", type=int, default=12)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        users=max(3, args.users),
        configs=max(4, args.configs),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    speedup = float(result["speedup_p50"])
    if args.min_speedup > 0 and speedup < args.min_speedup:
        raise SystemExit(
            "recommend evolution prepare-cache guardrail failed: "
            f"speedup={speedup} < {args.min_speedup}"
        )


if __name__ == "__main__":
    main()
