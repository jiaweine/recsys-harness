from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.text import cosine
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


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    rows: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        rows.append((time.perf_counter() - started) * 1000.0)
    return rows


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
    return Catalog(items=rows, interactions=interactions, name="recommend-run-benchmark"), user_id


def _legacy_prepare(engine, user_id: str) -> list[dict]:
    profile, cats, seen, seeds = engine._profile(user_id)
    dense_profile = engine._dense_profile(profile)
    cat_total = sum(cats.values()) or 1.0
    graph_scores = engine._graph_scores(seeds)
    candidate_ids = CAPABILITIES.call(
        "recommend.candidate",
        engine.config.candidate_strategy,
        engine,
        user_id,
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )
    cold = len(engine._by_user.get(user_id, [])) == 0
    rows = []
    for item_id in dict.fromkeys(str(value) for value in candidate_ids):
        item = engine.catalog.item_by_id.get(item_id)
        if item is None or not item.eligible or item.item_id in seen:
            continue
        item_vector = engine._vectors[item.item_id]
        if not profile:
            profile_fit = 0.0
        elif dense_profile is not None and len(profile) > len(item_vector):
            profile_fit = max(
                0.0,
                sum(
                    value * dense_profile[key]
                    for key, value in item_vector.items()
                ),
            )
        else:
            profile_fit = max(0.0, cosine(profile, item_vector))
        cat_fit = sum(cats.get(category, 0.0) for category in item.categories) / cat_total
        graph = graph_scores.get(item.item_id, 0.0)
        popularity = engine._popularity[item.item_id]
        novelty = 1.0 - popularity
        explore = CAPABILITIES.call(
            "recommend.exploration",
            engine.config.exploration_strategy,
            engine,
            user_id,
            item,
            popularity,
        )
        cold_prior = (
            CAPABILITIES.call(
                "recommend.cold_start",
                engine.config.cold_start_strategy,
                engine,
                item,
                popularity,
                explore,
            )
            if cold
            else 0.0
        )
        rows.append(
            {
                "item": item,
                "profile_fit": profile_fit,
                "cat_fit": cat_fit,
                "graph": graph,
                "pop": popularity,
                "novelty": novelty,
                "explore": explore,
                "cold_prior": cold_prior,
            }
        )
    return rows


def _legacy_run(registry: ToolRegistry, user_id: str) -> dict:
    segment = registry.segment_router.recommend_segment(user_id)
    config = registry.recommend_portfolio.get(segment)
    engine = registry.recommend.with_config(config) if config is not None else registry.recommend
    prepared = _legacy_prepare(engine, user_id)
    return {
        "user_id": user_id,
        "history_events": len(registry.recommend._by_user.get(user_id, [])),
        "segment": segment,
        "strategy_scope": "segment" if config is not None else "global",
        "results": engine.rank_prepared(prepared, limit=8),
    }


def _current_run(registry: ToolRegistry, user_id: str) -> dict:
    segment = registry.segment_router.recommend_segment(user_id)
    config = registry.recommend_portfolio.get(segment)
    engine = registry.recommend.with_config(config) if config is not None else registry.recommend
    return {
        "user_id": user_id,
        "history_events": len(registry.recommend._by_user.get(user_id, [])),
        "segment": segment,
        "strategy_scope": "segment" if config is not None else "global",
        "results": engine.recommend(user_id, limit=8),
    }


def run_benchmark(*, items: int, history: int, repeats: int) -> dict[str, object]:
    catalog, user_id = _catalog(items, history)
    with tempfile.TemporaryDirectory(prefix="xushu-recommend-run-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        registry = ToolRegistry(catalog, memory=memory)

        legacy_prepared = _legacy_prepare(registry.recommend, user_id)
        optimized_prepared = registry.recommend.prepare(user_id)
        if optimized_prepared != legacy_prepared:
            raise AssertionError("optimized prepare differs from legacy prepare")

        expected = _legacy_run(registry, user_id)
        actual = registry.run_recommend(user_id)
        if actual != expected:
            raise AssertionError("optimized run_recommend differs from legacy output")

        _legacy_prepare(registry.recommend, user_id)
        registry.recommend.prepare(user_id)
        _legacy_run(registry, user_id)
        registry.run_recommend(user_id)

        legacy_prepare = _summary(
            _timed(lambda: _legacy_prepare(registry.recommend, user_id), repeats)
        )
        optimized_prepare = _summary(
            _timed(lambda: registry.recommend.prepare(user_id), repeats)
        )
        legacy_full = _summary(
            _timed(lambda: _legacy_run(registry, user_id), repeats)
        )
        optimized_full = _summary(
            _timed(lambda: registry.run_recommend(user_id), repeats)
        )
        routing = _summary(
            _timed(lambda: registry.segment_router.recommend_segment(user_id), repeats)
        )

    prepare_speedup = float(legacy_prepare["p50_ms"]) / max(
        float(optimized_prepare["p50_ms"]),
        1e-9,
    )
    full_speedup = float(legacy_full["p50_ms"]) / max(
        float(optimized_full["p50_ms"]),
        1e-9,
    )
    routing_share = float(routing["p50_ms"]) / max(
        float(optimized_full["p50_ms"]),
        1e-9,
    )
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "legacy_prepare": legacy_prepare,
        "optimized_prepare": optimized_prepare,
        "prepare_speedup_p50": round(prepare_speedup, 2),
        "legacy_full_run": legacy_full,
        "optimized_full_run": optimized_full,
        "full_speedup_p50": round(full_speedup, 2),
        "routing": routing,
        "routing_share_p50": round(routing_share, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark full owned recommendation run.")
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-full-speedup", type=float, default=0.0)
    args = parser.parse_args()
    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(0, args.history),
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
            "recommend run performance guardrail failed: " + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
