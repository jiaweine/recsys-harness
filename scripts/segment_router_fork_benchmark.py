from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.algorithms import RecommendConfig, RecommendationEngine, SearchConfig
from lingjing_harness.domain import Catalog
from lingjing_harness.production import ExposureEvent, RewardSpec
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


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


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    out: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _catalog(requests_per_surface: int) -> Catalog:
    base = build_sample_catalog()
    labels = list(base.query_labels)
    users = RecommendationEngine(base).known_users()
    items = list(base.items)
    events: list[ExposureEvent] = []

    for index in range(requests_per_surface):
        label = labels[index % len(labels)]
        events.append(
            ExposureEvent(
                request_id=f"search-{index:05d}",
                timestamp=float(index),
                surface="search",
                query=label.query,
                item_id=items[index % len(items)].item_id,
                event="impression",
                position=1,
                propensity=0.5,
            )
        )

    for index in range(requests_per_surface):
        user_id = users[index % len(users)] if index % 7 else f"new-{index:05d}"
        events.append(
            ExposureEvent(
                request_id=f"recommend-{index:05d}",
                timestamp=float(requests_per_surface + index),
                surface="recommend",
                user_id=user_id,
                item_id=items[index % len(items)].item_id,
                event="impression",
                position=1,
                propensity=0.5,
            )
        )

    return Catalog(
        items=items,
        interactions=list(base.interactions),
        query_labels=list(base.query_labels),
        events=events,
        reward_spec=RewardSpec(weights={"impression": 0.0, "click": 1.0}),
        name="segment-router-fork-benchmark",
    )


def _legacy_fork(registry: ToolRegistry) -> ToolRegistry:
    clone = object.__new__(type(registry))
    clone.catalog = registry.catalog
    clone.memory = registry.memory
    clone.network = registry.network
    clone.catalog_key = registry.catalog_key
    clone.rollback_events = []
    clone.search = registry.search.with_config(clone._load_config("search", SearchConfig))
    clone.recommend = registry.recommend.with_config(clone._load_config("recommend", RecommendConfig))
    clone._specs = clone._build_specs()
    clone._refresh_portfolio()
    clone._validate_active_portfolio()
    return clone


def run_benchmark(*, requests_per_surface: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(requests_per_surface)
    with tempfile.TemporaryDirectory(prefix="xushu-segment-fork-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        initial = ToolRegistry(catalog, memory=memory)
        query = catalog.query_labels[0].query
        segment = initial.segment_router.search_segment(query)
        config = asdict(SearchConfig())
        config["diversity"] = 0.11
        memory.remember_strategy(
            initial.catalog_key,
            f"search.segment.{segment.split('/', 1)[1]}",
            config,
            score=0.8,
            evidence=requests_per_surface,
            status="active",
            payload={"validated_at": time.time()},
        )
        registry = ToolRegistry(catalog, memory=memory)

        legacy = _legacy_fork(registry)
        fast = registry.fork()
        if legacy.segment_router.search_thresholds != fast.segment_router.search_thresholds:
            raise AssertionError("search routing thresholds changed")
        if legacy.segment_router.recommend_thresholds != fast.segment_router.recommend_thresholds:
            raise AssertionError("recommend routing thresholds changed")
        if legacy.run_search(query)["segment"] != fast.run_search(query)["segment"]:
            raise AssertionError("search routing changed")

        legacy_samples = _timed(lambda: _legacy_fork(registry), repeats)
        fast_samples = _timed(lambda: registry.fork(), repeats)

    legacy_summary = _summary(legacy_samples)
    fast_summary = _summary(fast_samples)
    speedup = float(legacy_summary["p50_ms"]) / max(float(fast_summary["p50_ms"]), 1e-9)
    return {
        "requests_per_surface": requests_per_surface,
        "repeats": repeats,
        "legacy_fork": legacy_summary,
        "optimized_fork": fast_summary,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark production segment-router task forks.")
    parser.add_argument("--requests-per-surface", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        requests_per_surface=max(20, args.requests_per_surface),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized_fork"]["p50_ms"])
    speedup = float(result["speedup_p50"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(f"optimized fork p50={optimized} > {args.max_optimized_p50_ms}")
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"fork speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit("segment router fork performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
