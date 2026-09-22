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

        expected = _current_run(registry, user_id)
        actual = registry.run_recommend(user_id)
        if actual != expected:
            raise AssertionError("run_recommend differs from current routing + serving path")

        registry.segment_router.recommend_segment(user_id)
        registry.recommend.recommend(user_id, limit=8)
        registry.run_recommend(user_id)

        engine = registry.recommend
        profile, _cats, _seen, _seeds = engine._profile(user_id)
        dense_profile = engine._dense_profile(profile)
        eligible = [item for item in catalog.items if item.eligible]
        explore_handler = CAPABILITIES.resolve(
            "recommend.exploration",
            engine.config.exploration_strategy,
        ).handler

        registry_explore = _summary(_timed(
            lambda: [
                CAPABILITIES.call(
                    "recommend.exploration",
                    engine.config.exploration_strategy,
                    engine,
                    user_id,
                    item,
                    engine._popularity[item.item_id],
                )
                for item in eligible
            ],
            max(3, repeats // 2),
        ))
        direct_explore = _summary(_timed(
            lambda: [
                explore_handler(
                    engine,
                    user_id,
                    item,
                    engine._popularity[item.item_id],
                )
                for item in eligible
            ],
            max(3, repeats // 2),
        ))
        profile_dot = _summary(_timed(
            lambda: [
                sum(value * dense_profile[key] for key, value in engine._vectors[item.item_id].items())
                for item in eligible
            ] if dense_profile is not None else [],
            max(3, repeats // 2),
        ))

        routing = _summary(
            _timed(lambda: registry.segment_router.recommend_segment(user_id), repeats)
        )
        serving = _summary(
            _timed(lambda: registry.recommend.recommend(user_id, limit=8), repeats)
        )
        full = _summary(
            _timed(lambda: registry.run_recommend(user_id), repeats)
        )

    routing_share = float(routing["p50_ms"]) / max(float(full["p50_ms"]), 1e-9)
    return {
        "items": items,
        "history": history,
        "repeats": repeats,
        "routing": routing,
        "serving": serving,
        "full_run": full,
        "routing_share_p50": round(routing_share, 4),
        "registry_explore": registry_explore,
        "direct_explore": direct_explore,
        "profile_dot": profile_dot,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark full owned recommendation run.")
    parser.add_argument("--items", type=int, default=30_000)
    parser.add_argument("--history", type=int, default=5_000)
    parser.add_argument("--repeats", type=int, default=7)
    args = parser.parse_args()
    print(json.dumps(run_benchmark(
        items=max(1_000, args.items),
        history=max(10, args.history),
        repeats=max(3, args.repeats),
    ), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
