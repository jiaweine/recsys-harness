from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms import RecommendationEngine, SearchEngine
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    rows = sorted(values)
    return rows[min(len(rows) - 1, max(0, math.ceil(q * len(rows)) - 1))]


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
    return Catalog(
        items=[
            Item(
                item_id=f"item-{index}",
                title=f"Shared Item {index}",
                text=(
                    f"shared item description {index} common catalog semantic text "
                    f"family {index % 97} cohort {index % 31}"
                ),
                categories=["common", f"group-{index % 32}"],
                popularity=float((index * 7919) % 100_000),
                quality=0.65 + (index % 20) / 100.0,
                freshness=0.60 + (index % 25) / 100.0,
            )
            for index in range(size)
        ],
        name="shared-item-vector-benchmark",
    )


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    catalog = _catalog(items)

    legacy_samples, legacy_pair = _timed(
        lambda: (SearchEngine(catalog), RecommendationEngine(catalog)),
        repeats,
    )
    if not isinstance(legacy_pair, tuple) or len(legacy_pair) != 2:
        raise AssertionError("legacy engine pair construction failed")

    samples, registry = _timed(
        lambda: ToolRegistry(catalog, AgentMemory(":memory:")),
        repeats,
    )
    if not isinstance(registry, ToolRegistry):
        raise AssertionError("registry construction failed")

    search_vectors = registry.search._vectors  # noqa: SLF001 - benchmark contract
    recommend_vectors = registry.recommend._vectors  # noqa: SLF001 - benchmark contract
    if set(search_vectors) != set(recommend_vectors):
        raise AssertionError("search/recommend vector coverage diverged")

    return {
        "items": items,
        "repeats": repeats,
        "legacy_engine_pair_init": _summary(legacy_samples),
        "tool_registry_init": _summary(samples),
        "search_vectors": len(search_vectors),
        "recommend_vectors": len(recommend_vectors),
        "shared_vector_object": search_vectors is recommend_vectors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark duplicate item-vector construction across runtime engines."
    )
    parser.add_argument("--items", type=int, default=6000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-registry-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1000, args.items),
        repeats=max(2, args.repeats),
    )
    legacy_p50 = float(result["legacy_engine_pair_init"]["p50_ms"])
    registry_p50 = float(result["tool_registry_init"]["p50_ms"])
    speedup = legacy_p50 / max(registry_p50, 1e-9)
    result["init_speedup_p50"] = round(speedup, 2)
    print(json.dumps(result, sort_keys=True))

    failures: list[str] = []
    if not bool(result["shared_vector_object"]):
        failures.append("runtime engines do not share the same vector snapshot")
    if args.max_registry_p50_ms > 0 and registry_p50 > args.max_registry_p50_ms:
        failures.append(
            f"registry p50={registry_p50} > {args.max_registry_p50_ms}"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"init speedup={speedup:.2f} < {args.min_speedup}")
    if failures:
        raise SystemExit("shared item vector guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
