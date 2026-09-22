from __future__ import annotations

import argparse
import cProfile
import io
import json
import pstats
import statistics
import time
from collections import defaultdict

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(
    *,
    items: int,
    background_users: int,
    evaluated_users: int,
    history: int,
) -> tuple[Catalog, RecommendationEngine, list[str]]:
    rows = [
        Item(
            item_id=f"item-{index:06d}",
            title=f"History Item {index}",
            text="recommend relevance temporal history profile",
            categories=[f"cat-{index % 31}", f"cluster-{index % 67}"],
            popularity=float((items - index) % 997),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0

    def add_user(user_id: str, seed: int) -> None:
        nonlocal timestamp
        for offset in range(history):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[(seed * history + offset) % items].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0

    for user_index in range(background_users):
        add_user(f"bg-{user_index:05d}", user_index)

    evaluated = [f"zz-eval-{index:03d}" for index in range(evaluated_users)]
    for index, user_id in enumerate(evaluated, start=background_users):
        add_user(user_id, index)

    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog), evaluated


def _median_ms(fn, repeats: int) -> float:
    values = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        values.append((time.perf_counter() - started) * 1000.0)
    return statistics.median(values)


def run_profile(
    *,
    items: int,
    background_users: int,
    evaluated_users: int,
    history: int,
    repeats: int,
) -> dict[str, object]:
    catalog, engine, evaluated = _fixture(
        items=items,
        background_users=background_users,
        evaluated_users=evaluated_users,
        history=history,
    )

    def full_prepare():
        return validation.prepare_recommend_relevance(
            catalog,
            engine,
            users_override=evaluated,
            k=8,
        )

    users = validation._evaluation_users(
        catalog,
        evaluated,
        minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
    )
    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        by_user[event.user_id].append(event)
    targets = [
        validation._latest_novel_target(
            by_user[user_id],
            minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
        )
        for user_id in users
    ]
    targets = [target for target in targets if target is not None]

    evaluation_users_ms = _median_ms(
        lambda: validation._evaluation_users(
            catalog,
            evaluated,
            minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
        ),
        repeats,
    )

    def rebuild_groups_and_targets():
        current: dict[str, list[Interaction]] = defaultdict(list)
        for event in catalog.interactions:
            current[event.user_id].append(event)
        return [
            validation._latest_novel_target(
                current[user_id],
                minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
            )
            for user_id in users
        ]

    grouping_targets_ms = _median_ms(rebuild_groups_and_targets, repeats)

    def cutoff_scans():
        return [
            [
                event
                for event in catalog.interactions
                if event.timestamp < target.timestamp
            ]
            for target in targets
        ]

    cutoff_scans_ms = _median_ms(cutoff_scans, repeats)

    training_sets = cutoff_scans()

    def temporal_catalogs():
        return [
            validation._temporal_training_catalog(
                catalog,
                training,
                user_id=user_id,
                engine=engine,
            )
            for user_id, training in zip(users, training_sets, strict=True)
        ]

    catalog_build_ms = _median_ms(temporal_catalogs, repeats)
    catalogs = temporal_catalogs()

    def temporal_engines():
        return [
            validation._temporal_recommendation_engine(engine, current)
            for current in catalogs
        ]

    engine_build_ms = _median_ms(temporal_engines, repeats)
    full_prepare_ms = _median_ms(full_prepare, repeats)

    profiler = cProfile.Profile()
    profiler.enable()
    full_prepare()
    profiler.disable()
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats("cumtime").print_stats(18)

    prepared = full_prepare()
    return {
        "items": items,
        "interactions": len(catalog.interactions),
        "background_users": background_users,
        "evaluated_users": len(evaluated),
        "history": history,
        "prepared_slices": len(prepared.slices),
        "median_ms": {
            "full_prepare": round(full_prepare_ms, 3),
            "evaluation_users": round(evaluation_users_ms, 3),
            "grouping_and_targets": round(grouping_targets_ms, 3),
            "cutoff_scans": round(cutoff_scans_ms, 3),
            "temporal_catalogs": round(catalog_build_ms, 3),
            "temporal_engines": round(engine_build_ms, 3),
        },
        "cprofile": stream.getvalue(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--items", type=int, default=20_000)
    parser.add_argument("--background-users", type=int, default=500)
    parser.add_argument("--evaluated-users", type=int, default=10)
    parser.add_argument("--history", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    print(
        json.dumps(
            run_profile(
                items=args.items,
                background_users=args.background_users,
                evaluated_users=args.evaluated_users,
                history=args.history,
                repeats=args.repeats,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
