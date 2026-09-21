from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.domain import Catalog, Interaction
from lingjing_harness.production import ExposureEvent, RewardSpec, request_groups
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


def _catalog(*, requests_per_surface: int, interactions: int) -> Catalog:
    base = build_sample_catalog()
    items = list(base.items)
    labels = list(base.query_labels)
    seed_interactions = list(base.interactions)
    user_ids = sorted({row.user_id for row in seed_interactions})

    interaction_rows = [
        Interaction(
            user_id=seed_interactions[index % len(seed_interactions)].user_id,
            item_id=seed_interactions[index % len(seed_interactions)].item_id,
            event=seed_interactions[index % len(seed_interactions)].event,
            weight=seed_interactions[index % len(seed_interactions)].weight,
            timestamp=float(index),
        )
        for index in range(interactions)
    ]

    events: list[ExposureEvent] = []
    for index in range(requests_per_surface):
        events.append(
            ExposureEvent(
                request_id=f"search-{index:05d}",
                timestamp=float(index),
                surface="search",
                query=labels[index % len(labels)].query,
                item_id=items[index % len(items)].item_id,
                event="impression",
                position=1,
                propensity=0.5,
            )
        )
    for index in range(requests_per_surface):
        user_id = user_ids[index % len(user_ids)] if index % 7 else f"cold-{index:05d}"
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
        interactions=interaction_rows,
        query_labels=labels,
        events=events,
        reward_spec=RewardSpec(weights={"impression": 0.0, "click": 1.0}),
        name="data-inspect-benchmark",
    )


def _legacy_manifest(registry: ToolRegistry, surface: str) -> dict:
    router = registry.segment_router
    copied_events = list(router.catalog.events)
    partitions = router.partition_events(copied_events, surface=surface)
    if surface == "search":
        thresholds = dict(router.search_thresholds)
        contexts = len(router._search_calibration)
    else:
        thresholds = dict(router.recommend_thresholds)
        contexts = len(request_groups(copied_events, surface="recommend"))
    return {
        "surface": surface,
        "routing_basis": "production_traffic_quantiles",
        "contexts": contexts,
        "thresholds": thresholds,
        "requests_by_segment": {
            segment: len(request_groups(rows, surface=surface))
            for segment, rows in sorted(partitions.items())
        },
    }


def _legacy_inspect_hot_path(registry: ToolRegistry) -> dict:
    summary = registry.catalog.summary()
    issues: list[str] = []
    if summary["interactions"] == 0:
        issues.append("missing interactions")
    elif summary["users"] < 3:
        issues.append("too few users")
    if summary["queries"] == 0:
        issues.append("missing queries")
    elif summary["queries"] < 3:
        issues.append("too few queries")
    if summary["items"] < 12:
        issues.append("small catalog")
    duplicates = len(registry.catalog.items) - len(
        {item.title.strip().lower() for item in registry.catalog.items}
    )
    if duplicates:
        issues.append("duplicates")
    unavailable = sum(1 for item in registry.catalog.items if not item.eligible)
    if unavailable:
        issues.append("unavailable")
    return {
        "summary": summary,
        "issues": issues,
        "memory": registry.memory.stats(registry.catalog_key),
        "segment_routing": {
            "search": _legacy_manifest(registry, "search"),
            "recommend": _legacy_manifest(registry, "recommend"),
        },
    }


def run_benchmark(*, requests_per_surface: int, interactions: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(
        requests_per_surface=requests_per_surface,
        interactions=interactions,
    )
    with tempfile.TemporaryDirectory(prefix="xushu-data-inspect-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        registry = ToolRegistry(catalog, memory=memory)
        optimized = registry.inspect_data()
        legacy = _legacy_inspect_hot_path(registry)

        if legacy["summary"] != optimized["summary"]:
            raise AssertionError("catalog summary changed")
        if legacy["segment_routing"] != optimized["segment_routing"]:
            raise AssertionError("segment routing manifest changed")

        registry.inspect_data()
        legacy_samples = _timed(lambda: _legacy_inspect_hot_path(registry), repeats)
        optimized_samples = _timed(registry.inspect_data, repeats)

    legacy_summary = _summary(legacy_samples)
    optimized_summary = _summary(optimized_samples)
    speedup = float(legacy_summary["p50_ms"]) / max(float(optimized_summary["p50_ms"]), 1e-9)
    return {
        "requests_per_surface": requests_per_surface,
        "interactions": interactions,
        "repeats": repeats,
        "legacy_inspect": legacy_summary,
        "optimized_inspect": optimized_summary,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark data.inspect steady-state hot path.")
    parser.add_argument("--requests-per-surface", type=int, default=300)
    parser.add_argument("--interactions", type=int, default=20_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        requests_per_surface=max(20, args.requests_per_surface),
        interactions=max(1_000, args.interactions),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized_inspect"]["p50_ms"])
    speedup = float(result["speedup_p50"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(f"optimized inspect p50={optimized} > {args.max_optimized_p50_ms}")
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"inspect speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit("data inspect performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
