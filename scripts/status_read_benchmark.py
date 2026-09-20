from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Callable

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.production import ExposureEvent, RewardSpec
from lingjing_harness.runtime.memory import AgentMemory


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


def _timed(fn: Callable[[], object], repeats: int) -> list[float]:
    out: list[float] = []
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        fn()
        out.append((time.perf_counter() - started) * 1000.0)
    return out


def _catalog(
    *,
    items: int,
    interactions: int,
    events: int,
) -> Catalog:
    item_rows = [
        Item(
            item_id=f"item-{index}",
            title=f"Item {index}",
            categories=[f"category-{index % 80}", f"group-{index % 17}"],
            popularity=float(index % 100),
            quality=0.7,
            freshness=0.8,
        )
        for index in range(items)
    ]
    interaction_rows = [
        Interaction(
            user_id=f"user-{index % 25_000}",
            item_id=f"item-{index % items}",
            event="click",
            weight=1.0,
            timestamp=float(index),
        )
        for index in range(interactions)
    ]
    event_rows = [
        ExposureEvent(
            request_id=f"request-{index // 5}",
            timestamp=float(index),
            surface="search" if index % 2 == 0 else "recommend",
            item_id=f"item-{index % items}",
            event="impression",
            position=(index % 10) + 1,
        )
        for index in range(events)
    ]
    return Catalog(
        items=item_rows,
        interactions=interaction_rows,
        events=event_rows,
        reward_spec=RewardSpec(weights={"click": 1.0}),
        name="status-benchmark",
    )


def _seed_memory(
    memory: AgentMemory,
    *,
    catalog_key: str,
    episodes: int,
    skills: int,
    credit_arms: int,
    credit_events: int,
) -> None:
    path = memory.path
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            insert into agent_episodes(
              catalog_key,goal,mode,reward,payload,created_at
            ) values(?,?,?,?,?,?)
            """,
            [
                (
                    catalog_key,
                    f"goal {index}",
                    "search" if index % 2 == 0 else "recommend",
                    0.5,
                    "{}",
                    float(index),
                )
                for index in range(episodes)
            ],
        )
        connection.executemany(
            """
            insert into agent_skills(
              catalog_key,domain,fingerprint,config,score,evidence,status,wins,payload,
              created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    catalog_key,
                    "search" if index % 2 == 0 else "recommend",
                    f"skill-{index}",
                    "{}",
                    0.5,
                    10,
                    "active" if index % 10 == 0 else "trusted",
                    1,
                    "{}",
                    float(index),
                    float(index),
                )
                for index in range(skills)
            ],
        )
        connection.executemany(
            """
            insert into agent_strategy_credit(
              catalog_key,domain,arm_key,positive,negative,trials,reward_sum,evidence,
              last_outcome,last_reason,created_at,updated_at
            ) values(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    catalog_key,
                    "search" if index % 2 == 0 else "recommend",
                    f"arm-{index}",
                    3 if index % 3 else 1,
                    2 if index % 3 else 4,
                    5,
                    2.5,
                    10,
                    "accepted",
                    "benchmark",
                    float(index),
                    float(index),
                )
                for index in range(credit_arms)
            ],
        )
        connection.executemany(
            """
            insert into agent_strategy_credit_events(
              event_key,catalog_key,domain,arm_key,outcome,reward_delta,evidence,reason,
              payload,created_at
            ) values(?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    f"event-{index}",
                    catalog_key,
                    "search" if index % 2 == 0 else "recommend",
                    f"arm-{index % max(1, credit_arms)}",
                    "accepted",
                    0.1,
                    10,
                    "benchmark",
                    "{}",
                    float(index),
                )
                for index in range(credit_events)
            ],
        )
        connection.commit()


def run_benchmark(
    *,
    items: int,
    interactions: int,
    events: int,
    episodes: int,
    skills: int,
    credit_arms: int,
    credit_events: int,
    repeats: int,
) -> dict[str, object]:
    catalog = _catalog(
        items=items,
        interactions=interactions,
        events=events,
    )
    expected_catalog = catalog.summary()

    import lingjing_harness.api_core as api_core

    api_core.catalog = catalog
    api_core.CATALOG_REVISION = "status-benchmark-revision"
    api_core._CATALOG_SUMMARY_CACHE = None
    cold_cached_catalog = _timed(api_core._catalog_summary, 1)
    warm_cached_catalog = _timed(api_core._catalog_summary, repeats)

    with tempfile.TemporaryDirectory(prefix="xushu-status-read-") as directory:
        memory = AgentMemory(Path(directory) / "agent-memory.db")
        key = "catalog-benchmark"
        _seed_memory(
            memory,
            catalog_key=key,
            episodes=episodes,
            skills=skills,
            credit_arms=credit_arms,
            credit_events=credit_events,
        )
        expected_memory = memory.stats(key)

        catalog_samples = _timed(catalog.summary, repeats)
        memory_uncached_samples = _timed(lambda: memory._stats_uncached(key), repeats)
        memory_samples = _timed(lambda: memory.stats(key), repeats)
        combined_samples = _timed(
            lambda: (catalog.summary(), memory._stats_uncached(key)),
            repeats,
        )
        combined_cached_catalog = _timed(
            lambda: (api_core._catalog_summary(), memory.stats(key)),
            repeats,
        )

        if catalog.summary() != expected_catalog:
            raise AssertionError("catalog summary changed during benchmark")
        if memory.stats(key) != expected_memory:
            raise AssertionError("memory stats changed during benchmark")

        return {
            "items": items,
            "interactions": interactions,
            "events": events,
            "episodes": episodes,
            "skills": skills,
            "credit_arms": credit_arms,
            "credit_events": credit_events,
            "catalog_summary": _summary(catalog_samples),
            "catalog_summary_cached_cold": _summary(cold_cached_catalog),
            "catalog_summary_cached_warm": _summary(warm_cached_catalog),
            "memory_stats_uncached": _summary(memory_uncached_samples),
            "memory_stats": _summary(memory_samples),
            "combined_status_data": _summary(combined_samples),
            "combined_status_cached_catalog": _summary(combined_cached_catalog),
            "catalog_result": expected_catalog,
            "memory_result": expected_memory,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark /api/status catalog and memory aggregation hot paths."
    )
    parser.add_argument("--items", type=int, default=10_000)
    parser.add_argument("--interactions", type=int, default=100_000)
    parser.add_argument("--events", type=int, default=100_000)
    parser.add_argument("--episodes", type=int, default=100_000)
    parser.add_argument("--skills", type=int, default=10_000)
    parser.add_argument("--credit-arms", type=int, default=10_000)
    parser.add_argument("--credit-events", type=int, default=100_000)
    parser.add_argument("--repeats", type=int, default=24)
    parser.add_argument("--max-warm-summary-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-warm-memory-p50-ms", type=float, default=0.0)
    parser.add_argument("--max-cached-combined-p50-ms", type=float, default=0.0)
    parser.add_argument("--min-memory-speedup", type=float, default=0.0)
    parser.add_argument("--min-combined-speedup", type=float, default=0.0)
    args = parser.parse_args()

    result = run_benchmark(
        items=max(100, args.items),
        interactions=max(1_000, args.interactions),
        events=max(1_000, args.events),
        episodes=max(1_000, args.episodes),
        skills=max(100, args.skills),
        credit_arms=max(100, args.credit_arms),
        credit_events=max(1_000, args.credit_events),
        repeats=max(5, args.repeats),
    )
    uncached = float(result["combined_status_data"]["p50_ms"])
    cached = float(result["combined_status_cached_catalog"]["p50_ms"])
    warm = float(result["catalog_summary_cached_warm"]["p50_ms"])
    memory_uncached = float(result["memory_stats_uncached"]["p50_ms"])
    memory_cached = float(result["memory_stats"]["p50_ms"])
    memory_speedup = memory_uncached / max(memory_cached, 1e-9)
    speedup = uncached / max(cached, 1e-9)
    result["memory_speedup_p50"] = round(memory_speedup, 2)
    result["combined_speedup_p50"] = round(speedup, 2)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))

    failures: list[str] = []
    if args.max_warm_summary_p50_ms > 0 and warm > args.max_warm_summary_p50_ms:
        failures.append(
            f"warm catalog summary p50={warm} > {args.max_warm_summary_p50_ms}"
        )
    if args.max_warm_memory_p50_ms > 0 and memory_cached > args.max_warm_memory_p50_ms:
        failures.append(
            f"warm memory stats p50={memory_cached} > {args.max_warm_memory_p50_ms}"
        )
    if args.max_cached_combined_p50_ms > 0 and cached > args.max_cached_combined_p50_ms:
        failures.append(
            f"cached combined p50={cached} > {args.max_cached_combined_p50_ms}"
        )
    if args.min_memory_speedup > 0 and memory_speedup < args.min_memory_speedup:
        failures.append(
            f"memory speedup={memory_speedup:.2f} < {args.min_memory_speedup}"
        )
    if args.min_combined_speedup > 0 and speedup < args.min_combined_speedup:
        failures.append(
            f"combined speedup={speedup:.2f} < {args.min_combined_speedup}"
        )
    if failures:
        raise SystemExit("status read performance guardrail failed: " + "; ".join(failures))


if __name__ == "__main__":
    main()
