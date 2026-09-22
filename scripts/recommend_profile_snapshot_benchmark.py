from __future__ import annotations

import argparse
import gc
import json
import math
import statistics
import time
from typing import Callable

from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.recommend_core import RecommendationEngine
from lingjing_harness.algorithms.text import cosine
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
    return Catalog(items=rows, interactions=interactions), user_id


def _legacy_prepare(engine: RecommendationEngine, user_id: str) -> list[dict]:
    """Literal pre-snapshot prepare path from current main."""

    profile, cats, seen, seeds = engine._profile(user_id)
    dense_profile = engine._dense_profile(profile)
    cat_total = sum(cats.values()) or 1.0
    graph_scores = engine._graph_scores(seeds)

    candidate_spec = CAPABILITIES.resolve(
        "recommend.candidate",
        engine.config.candidate_strategy,
    )
    if candidate_spec.name == "full_pool":
        candidate_items = (
            item
            for item in engine.catalog.items
            if item.eligible and item.item_id not in seen
        )
    else:
        candidate_ids = candidate_spec.handler(
            engine,
            user_id,
            profile,
            cats,
            seen,
            seeds,
            graph_scores,
        )

        def resolved_items():
            for item_id in dict.fromkeys(str(value) for value in candidate_ids):
                item = engine.catalog.item_by_id.get(item_id)
                if item is None or not item.eligible or item.item_id in seen:
                    continue
                yield item

        candidate_items = resolved_items()

    cold = len(engine._by_user.get(user_id, [])) == 0
    explore_handler = CAPABILITIES.resolve(
        "recommend.exploration",
        engine.config.exploration_strategy,
    ).handler
    cold_handler = (
        CAPABILITIES.resolve(
            "recommend.cold_start",
            engine.config.cold_start_strategy,
        ).handler
        if cold
        else None
    )
    rows = []
    vectors = engine._vectors
    popularity_by_id = engine._popularity
    dense_values = dense_profile
    for item in candidate_items:
        item_vector = vectors[item.item_id]
        if not profile:
            profile_fit = 0.0
        elif dense_values is not None and len(profile) > len(item_vector):
            profile_fit = max(
                0.0,
                sum(
                    value * dense_values[key]
                    for key, value in item_vector.items()
                ),
            )
        else:
            profile_fit = max(0.0, cosine(profile, item_vector))
        cat_fit = sum(cats.get(category, 0.0) for category in item.categories) / cat_total
        graph = graph_scores.get(item.item_id, 0.0)
        popularity = popularity_by_id[item.item_id]
        novelty = 1.0 - popularity
        explore = explore_handler(engine, user_id, item, popularity)
        cold_prior = (
            cold_handler(engine, item, popularity, explore)
            if cold_handler is not None
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


def run_benchmark(*, items: int, history: int, repeats: int, limit: int) -> dict[str, object]:
    catalog, user_id = _catalog(items, history)
    engine = RecommendationEngine(catalog)

    legacy_prepared = _legacy_prepare(engine, user_id)
    optimized_prepared = engine.prepare(user_id)
    if optimized_prepared != legacy_prepared:
        raise AssertionError("profile snapshot changed prepared rows")

    legacy_result = engine.rank_prepared(legacy_prepared, limit=limit)
    optimized_result = engine.recommend(user_id, limit=limit)
    if optimized_result != legacy_result:
        raise AssertionError("profile snapshot changed recommendation output")

    # Warm the private owned snapshot. The literal legacy path never reads it.
    engine.prepare(user_id)

    legacy_prepare_samples, optimized_prepare_samples = _paired_timed(
        lambda: _legacy_prepare(engine, user_id),
        lambda: engine.prepare(user_id),
        repeats,
    )
    legacy_full_samples, optimized_full_samples = _paired_timed(
        lambda: engine.rank_prepared(_legacy_prepare(engine, user_id), limit=limit),
        lambda: engine.recommend(user_id, limit=limit),
        repeats,
    )

    legacy_prepare = _summary(legacy_prepare_samples)
    optimized_prepare = _summary(optimized_prepare_samples)
    legacy_full = _summary(legacy_full_samples)
    optimized_full = _summary(optimized_full_samples)
    return {
        "items": items,
        "history": history,
        "limit": limit,
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
        description="Benchmark private owned recommendation profile snapshots."
    )
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=7)
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--max-optimized-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-prepare-speedup", type=float, default=0.0)
    parser.add_argument("--min-full-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(1_000, args.items),
        history=max(1, args.history),
        repeats=max(3, args.repeats),
        limit=max(1, args.limit),
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
            "recommend profile snapshot performance guardrail failed: "
            + "; ".join(failures)
        )


if __name__ == "__main__":
    main()
