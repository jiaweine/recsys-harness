from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.domain import Catalog, Item
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.runtime.tools_core import ToolRegistry as CoreToolRegistry


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


def _catalog(items: int) -> Catalog:
    return Catalog(
        items=[
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
        ],
        name="search-diagnose-route-benchmark",
    )


def _legacy_diagnose(registry: ToolRegistry, query: str) -> dict:
    result = CoreToolRegistry.search_diagnose(registry, query=query)
    segment = registry.segment_router.search_segment(query)
    return {
        **result,
        "segment": segment,
        "strategy_scope": "segment" if segment in registry.search_portfolio else "global",
    }


def run_benchmark(*, items: int, repeats: int) -> dict[str, object]:
    query = "wireless headphones"
    catalog = _catalog(items)
    with tempfile.TemporaryDirectory(prefix="xushu-search-diagnose-route-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        registry = ToolRegistry(catalog, memory=memory)

        legacy_value = _legacy_diagnose(registry, query)
        optimized_value = registry.search_diagnose(query)
        if legacy_value != optimized_value:
            raise AssertionError("stable search diagnosis response changed")

        _legacy_diagnose(registry, query)
        registry.search_diagnose(query)
        legacy_samples = _timed(lambda: _legacy_diagnose(registry, query), repeats)
        optimized_samples = _timed(lambda: registry.search_diagnose(query), repeats)

    legacy = _summary(legacy_samples)
    optimized = _summary(optimized_samples)
    speedup = float(legacy["p50_ms"]) / max(float(optimized["p50_ms"]), 1e-9)
    return {
        "items": items,
        "repeats": repeats,
        "legacy_diagnose": legacy,
        "optimized_diagnose": optimized,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark stable search.diagnose routing prepare reuse."
    )
    parser.add_argument("--items", type=int, default=6000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(items=max(1000, args.items), repeats=max(3, args.repeats))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized_diagnose"]["p50_ms"])
    speedup = float(result["speedup_p50"])
    failures: list[str] = []
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(f"optimized p50={optimized}ms > {args.max_optimized_p50_ms}ms")
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "search diagnose route performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
