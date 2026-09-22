from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from types import SimpleNamespace
from typing import Callable

from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.text import tokenize
from lingjing_harness.domain import Catalog, Item
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


def _registry(items: int) -> ToolRegistry:
    catalog = Catalog(
        items=[
            Item(
                item_id=f"item-{index:06d}",
                title=("alpha" if index % 2 == 0 else "beta") + f" item {index}",
            )
            for index in range(items)
        ]
    )
    positions = [
        max(0, min(items - 1, int(items * fraction)))
        for fraction in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80)
    ]
    rows = [
        {
            "id": f"item-{index:06d}",
            "title": catalog.item_by_id[f"item-{index:06d}"].title,
            "signals": {"match": 0.8},
        }
        for index in positions
    ]
    registry = object.__new__(ToolRegistry)
    registry.catalog = catalog
    registry.search = SimpleNamespace(search=lambda query, limit: rows)
    return registry


def _legacy_search_diagnose(registry: ToolRegistry, query: str) -> dict:
    query = (query or "").strip()
    query_tokens = list(dict.fromkeys(tokenize(query)))
    result = registry.search.search(query, limit=8)
    covered = set()
    for row in result:
        title = next(
            (
                item.title
                for item in registry.catalog.items
                if item.item_id == row["id"]
            ),
            row["title"],
        )
        covered.update(set(query_tokens) & set(tokenize(title)))
    generic = [
        token for token in query_tokens if token in SearchEngine.GENERIC_QUERY_TOKENS
    ]
    return {
        "query": query,
        "query_tokens": query_tokens,
        "covered_tokens": sorted(covered),
        "generic_tokens": generic,
        "result_count": len(result),
        "top_match": result[0]["signals"]["match"] if result else 0.0,
        "diagnosis": (
            "没有候选包含可验证的查询词证据"
            if not result
            else "查询包含较宽泛词，排序需要更多依赖具体词证据"
            if generic
            else "当前候选存在但首位匹配证据偏弱"
            if result[0]["signals"]["match"] < 0.42
            else "当前查询的直接词项证据基本完整"
        ),
    }


def run_benchmark(*, items: int, repeats: int, calls_per_sample: int) -> dict[str, object]:
    registry = _registry(items)
    query = "alpha beta"
    legacy_value = _legacy_search_diagnose(registry, query)
    optimized_value = registry.search_diagnose(query)
    if legacy_value != optimized_value:
        raise AssertionError("search diagnosis output changed")

    legacy = _summary(
        _timed_per_call(
            lambda: _legacy_search_diagnose(registry, query),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    optimized = _summary(
        _timed_per_call(
            lambda: registry.search_diagnose(query),
            repeats=repeats,
            calls_per_sample=calls_per_sample,
        )
    )
    speedup = float(legacy["p50_us"]) / max(float(optimized["p50_us"]), 1e-9)
    return {
        "items": items,
        "results": 8,
        "repeats": repeats,
        "calls_per_sample": calls_per_sample,
        "legacy": legacy,
        "optimized": optimized,
        "speedup_p50": round(speedup, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark search diagnosis item lookup.")
    parser.add_argument("--items", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--calls-per-sample", type=int, default=3)
    parser.add_argument("--max-optimized-p50-us", type=float, default=0.0)
    parser.add_argument("--min-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        items=max(1_000, args.items),
        repeats=max(3, args.repeats),
        calls_per_sample=max(1, args.calls_per_sample),
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    optimized = float(result["optimized"]["p50_us"])
    speedup = float(result["speedup_p50"])
    failures: list[str] = []
    if args.max_optimized_p50_us > 0 and optimized > args.max_optimized_p50_us:
        failures.append(
            f"optimized p50={optimized}us > {args.max_optimized_p50_us}us"
        )
    if args.min_speedup > 0 and speedup < args.min_speedup:
        failures.append(f"speedup={speedup} < {args.min_speedup}")
    if failures:
        raise SystemExit(
            "search diagnose performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
