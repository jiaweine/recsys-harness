from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry


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


def _paired_timed(
    legacy_fn: Callable[[], object],
    optimized_fn: Callable[[], object],
    repeats: int,
) -> tuple[list[float], list[float]]:
    legacy: list[float] = []
    optimized: list[float] = []
    for index in range(repeats):
        ordered = (
            ((legacy_fn, legacy), (optimized_fn, optimized))
            if index % 2 == 0
            else ((optimized_fn, optimized), (legacy_fn, legacy))
        )
        for fn, bucket in ordered:
            gc.collect()
            was_enabled = gc.isenabled()
            gc.disable()
            try:
                started = time.perf_counter()
                fn()
                bucket.append((time.perf_counter() - started) * 1000.0)
            finally:
                if was_enabled:
                    gc.enable()
    return legacy, optimized


def _paired_speedup(legacy: list[float], optimized: list[float]) -> float:
    return statistics.median(
        legacy_ms / max(optimized_ms, 1e-9)
        for legacy_ms, optimized_ms in zip(legacy, optimized, strict=True)
    )


def _catalog(items: int, history: int) -> tuple[Catalog, str]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"Item {index}",
            text=f"catalog item {index} outdoor audio travel",
            categories=[f"cat-{index % 32}", f"cluster-{index % 97}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    user_id = "warm-user"
    interactions = [
        Interaction(
            user_id=user_id,
            item_id=rows[index % items].item_id,
            event="click",
            weight=1.0 + (index % 5) * 0.1,
            timestamp=float(index + 1),
        )
        for index in range(history)
    ]
    return Catalog(items=rows, interactions=interactions, name="profile-cache-benchmark"), user_id


def run_benchmark(*, items: int, history: int, repeats: int) -> dict[str, object]:
    catalog, user_id = _catalog(items, history)
    with tempfile.TemporaryDirectory(prefix="xushu-profile-cache-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        registry = ToolRegistry(catalog, memory=memory)
        engine = registry.recommend

        engine._profile_cache.clear()
        expected_prepared = engine.prepare(user_id)
        engine._profile_cache.clear()
        expected_run = registry.run_recommend(user_id)

        cached_prepared = engine.prepare(user_id)
        cached_run = registry.run_recommend(user_id)
        if cached_prepared != expected_prepared:
            raise AssertionError("cached profile changed recommendation preparation")
        if cached_run != expected_run:
            raise AssertionError("cached profile changed recommendation output")

        def uncached_prepare():
            engine._profile_cache.clear()
            return engine.prepare(user_id)

        def cached_prepare():
            return engine.prepare(user_id)

        def uncached_run():
            engine._profile_cache.clear()
            return registry.run_recommend(user_id)

        def cached_run():
            return registry.run_recommend(user_id)

        # Prime the steady cache before timing.
        engine._profile_cache.clear()
        engine._profile(user_id)

        legacy_prepare_samples, optimized_prepare_samples = _paired_timed(
            uncached_prepare,
            cached_prepare,
            repeats,
        )
        engine._profile_cache.clear()
        engine._profile(user_id)
        legacy_full_samples, optimized_full_samples = _paired_timed(
            uncached_run,
            cached_run,
            repeats,
        )

    legacy_prepare = _summary(legacy_prepare_samples)
    optimized_prepare = _summary(optimized_prepare_samples)
    legacy_full = _summary(legacy_full_samples)
    optimized_full = _summary(optimized_full_samples)
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "legacy_prepare": legacy_prepare,
        "optimized_prepare": optimized_prepare,
        "prepare_speedup_p50": round(
            _paired_speedup(legacy_prepare_samples, optimized_prepare_samples),
            2,
        ),
        "legacy_full_run": legacy_full,
        "optimized_full_run": optimized_full,
        "full_speedup_p50": round(
            _paired_speedup(legacy_full_samples, optimized_full_samples),
            2,
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark bounded warm recommendation profile caching."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-full-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(1, args.history),
        repeats=max(3, args.repeats),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    optimized = float(result["optimized_full_run"]["p50_ms"])
    prepare_speedup = float(result["prepare_speedup_p50"])
    full_speedup = float(result["full_speedup_p50"])
    if args.max_optimized_p50_ms > 0 and optimized > args.max_optimized_p50_ms:
        failures.append(
            f"optimized full-run p50={optimized}ms > {args.max_optimized_p50_ms}ms"
        )
    if args.min_prepare_speedup > 0 and prepare_speedup < args.min_prepare_speedup:
        failures.append(
            f"prepare speedup={prepare_speedup} < {args.min_prepare_speedup}"
        )
    if args.min_full_speedup > 0 and full_speedup < args.min_full_speedup:
        failures.append(
            f"full-run speedup={full_speedup} < {args.min_full_speedup}"
        )
    if failures:
        raise SystemExit(
            "recommend profile cache performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
