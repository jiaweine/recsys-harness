from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter, OrderedDict, defaultdict

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
            title=f"Snapshot Item {index}",
            text="recommend relevance temporal snapshot profile",
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


def _instrumented_states(
    catalog: Catalog,
    target_timestamps: list[float],
) -> dict[str, float | int]:
    started = time.perf_counter()
    chronological = sorted(
        catalog.interactions,
        key=lambda event: (event.timestamp, event.user_id, event.item_id),
    )
    sort_ms = (time.perf_counter() - started) * 1000.0

    requests = sorted(enumerate(target_timestamps), key=lambda row: (row[1], row[0]))
    by_user: defaultdict[str, list[Interaction]] = defaultdict(list)
    co: defaultdict[str, Counter[str]] = defaultdict(Counter)
    recent_by_user: dict[str, OrderedDict[str, None]] = {}
    cursor = 0
    advance_ms = 0.0
    by_user_copy_ms = 0.0
    co_copy_ms = 0.0
    by_user_rows_copied = 0
    co_edges_copied = 0

    for _, cutoff in requests:
        phase = time.perf_counter()
        while cursor < len(chronological) and chronological[cursor].timestamp < cutoff:
            event = chronological[cursor]
            cursor += 1
            by_user[event.user_id].append(event)

            recent = recent_by_user.setdefault(event.user_id, OrderedDict())
            if event.item_id in recent:
                recent.move_to_end(event.item_id)
                continue

            if len(recent) >= RecommendationEngine.MAX_GRAPH_HISTORY:
                evicted, _ = recent.popitem(last=False)
                for other in tuple(recent):
                    validation._decrement_graph_pair(co, evicted, other)
                    validation._decrement_graph_pair(co, other, evicted)

            for other in recent:
                co[event.item_id][other] += 1
                co[other][event.item_id] += 1
            recent[event.item_id] = None
        advance_ms += (time.perf_counter() - phase) * 1000.0

        phase = time.perf_counter()
        by_user_snapshot = defaultdict(
            list,
            {user_id: list(events) for user_id, events in by_user.items()},
        )
        by_user_copy_ms += (time.perf_counter() - phase) * 1000.0
        by_user_rows_copied += sum(len(events) for events in by_user_snapshot.values())

        phase = time.perf_counter()
        co_snapshot = defaultdict(
            Counter,
            {
                item_id: Counter(counts)
                for item_id, counts in co.items()
                if counts
            },
        )
        co_copy_ms += (time.perf_counter() - phase) * 1000.0
        co_edges_copied += sum(len(counts) for counts in co_snapshot.values())

    return {
        "sort_ms": sort_ms,
        "advance_graph_ms": advance_ms,
        "by_user_copy_ms": by_user_copy_ms,
        "co_copy_ms": co_copy_ms,
        "by_user_rows_copied": by_user_rows_copied,
        "co_edges_copied": co_edges_copied,
    }


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

    by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in catalog.interactions:
        by_user[event.user_id].append(event)
    targets = [
        validation._latest_novel_target(
            by_user[user_id],
            minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
        )
        for user_id in evaluated
    ]
    targets = [target for target in targets if target is not None]
    target_timestamps = [target.timestamp for target in targets]

    full_prepare_ms = _median_ms(
        lambda: validation.prepare_recommend_relevance(
            catalog,
            engine,
            users_override=evaluated,
            k=8,
        ),
        repeats,
    )
    state_builder_ms = _median_ms(
        lambda: validation._owned_temporal_states(catalog, target_timestamps),
        repeats,
    )

    phase_runs = [
        _instrumented_states(catalog, target_timestamps)
        for _ in range(repeats)
    ]
    phase_medians = {
        key: round(statistics.median(float(row[key]) for row in phase_runs), 3)
        for key in ("sort_ms", "advance_graph_ms", "by_user_copy_ms", "co_copy_ms")
    }

    def materialize_training_interactions() -> list[list[Interaction]]:
        return [
            [event for event in catalog.interactions if event.timestamp < cutoff]
            for cutoff in target_timestamps
        ]

    training_interactions_ms = _median_ms(materialize_training_interactions, repeats)
    training_sets = materialize_training_interactions()

    temporal_catalog_ms = _median_ms(
        lambda: [
            validation._temporal_training_catalog(
                catalog,
                training,
                user_id=user_id,
                engine=engine,
            )
            for user_id, training in zip(evaluated, training_sets, strict=True)
        ],
        repeats,
    )

    representative = phase_runs[-1]
    return {
        "items": items,
        "interactions": len(catalog.interactions),
        "background_users": background_users,
        "evaluated_users": evaluated_users,
        "history": history,
        "targets": len(target_timestamps),
        "median_ms": {
            "full_prepare": round(full_prepare_ms, 3),
            "owned_temporal_states": round(state_builder_ms, 3),
            **phase_medians,
            "training_interactions_materialization": round(training_interactions_ms, 3),
            "temporal_catalog_materialization": round(temporal_catalog_ms, 3),
        },
        "copy_volume": {
            "by_user_rows": int(representative["by_user_rows_copied"]),
            "co_directed_edges": int(representative["co_edges_copied"]),
        },
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
