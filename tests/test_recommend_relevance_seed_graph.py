from __future__ import annotations

from collections import Counter

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.recommend_temporal_graph import TemporalGraphSnapshot
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(
    *,
    items_count: int = 1600,
    background_users: int = 18,
    evaluated_users: int = 5,
    history: int = 28,
) -> tuple[Catalog, RecommendationEngine, list[str]]:
    items = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Seed Graph Item {index}",
            text="temporal seed graph exactness",
            categories=[f"cat-{index % 19}", f"cluster-{index % 31}"],
            popularity=float((items_count - index) % 113),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items_count)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0

    def add_user(user_id: str, seed: int) -> None:
        nonlocal timestamp
        for offset in range(history):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=items[(seed * 23 + offset * 3) % items_count].item_id,
                    event="click",
                    weight=1.0 + (offset % 3) * 0.1,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0

    for index in range(background_users):
        add_user(f"bg-{index:03d}", index)
    evaluated = [f"zz-eval-{index:02d}" for index in range(evaluated_users)]
    for index, user_id in enumerate(evaluated, start=background_users):
        add_user(user_id, index)

    catalog = Catalog(items=items, interactions=interactions)
    return catalog, RecommendationEngine(catalog), evaluated


def _target_rows(
    catalog: Catalog,
    evaluated: list[str],
) -> tuple[list[float], list[set[str]]]:
    by_user: dict[str, list[Interaction]] = {}
    for event in catalog.interactions:
        by_user.setdefault(event.user_id, []).append(event)

    timestamps: list[float] = []
    seeds: list[set[str]] = []
    for user_id in evaluated:
        target = validation._latest_novel_target(
            by_user[user_id],
            minimum_target_weight=validation.DEFAULT_MIN_TARGET_WEIGHT,
        )
        assert target is not None
        history = [event for event in by_user[user_id] if event.timestamp < target.timestamp]
        timestamps.append(target.timestamp)
        seeds.append({event.item_id for event in history})
    return timestamps, seeds


def test_seed_graph_rows_match_full_temporal_graph_without_materializing() -> None:
    catalog, engine, evaluated = _fixture()
    timestamps, seeds = _target_rows(catalog, evaluated)
    states = validation._owned_temporal_states(
        catalog,
        timestamps,
        seed_item_ids=seeds,
    )

    for user_id, cutoff, seed_ids, state in zip(
        evaluated,
        timestamps,
        seeds,
        states,
        strict=True,
    ):
        training_interactions = [
            event for event in catalog.interactions if event.timestamp < cutoff
        ]
        training_catalog = validation._temporal_training_catalog(
            catalog,
            training_interactions,
            user_id=user_id,
            engine=engine,
        )
        optimized = validation._owned_temporal_recommendation_engine(
            engine,
            training_catalog,
            state,
        )
        legacy = validation._temporal_recommendation_engine(engine, training_catalog)

        assert isinstance(optimized._co, TemporalGraphSnapshot)
        assert not optimized._co.materialized
        for seed_id in seed_ids:
            assert optimized._co.get(seed_id, {}) == legacy._co.get(seed_id, {})
        assert not optimized._co.materialized

        weighted_seeds = Counter({seed_id: 1.0 for seed_id in seed_ids})
        assert optimized._graph_scores(weighted_seeds) == legacy._graph_scores(weighted_seeds)
        assert not optimized._co.materialized


def test_seed_graph_lazy_fallback_recovers_exact_full_graph() -> None:
    catalog, engine, evaluated = _fixture()
    timestamps, seeds = _target_rows(catalog, evaluated)
    state = validation._owned_temporal_states(
        catalog,
        [timestamps[1]],
        seed_item_ids=[seeds[1]],
    )[0]
    training_interactions = [
        event for event in catalog.interactions if event.timestamp < timestamps[1]
    ]
    training_catalog = validation._temporal_training_catalog(
        catalog,
        training_interactions,
        user_id=evaluated[1],
        engine=engine,
    )
    optimized = validation._owned_temporal_recommendation_engine(
        engine,
        training_catalog,
        state,
    )
    legacy = validation._temporal_recommendation_engine(engine, training_catalog)

    assert isinstance(optimized._co, TemporalGraphSnapshot)
    assert not optimized._co.materialized
    assert {
        item_id: Counter(counts)
        for item_id, counts in optimized._co.items()
    } == {
        item_id: Counter(counts)
        for item_id, counts in legacy._co.items()
    }
    assert optimized._co.materialized


def test_absent_seed_get_preserves_defaultdict_get_semantics_without_fallback() -> None:
    catalog, engine, evaluated = _fixture()
    timestamps, seeds = _target_rows(catalog, evaluated)
    absent_seed = "item-01599"
    seed_ids = set(seeds[0])
    seed_ids.add(absent_seed)
    state = validation._owned_temporal_states(
        catalog,
        [timestamps[0]],
        seed_item_ids=[seed_ids],
    )[0]
    training_catalog = validation._temporal_training_catalog(
        catalog,
        [event for event in catalog.interactions if event.timestamp < timestamps[0]],
        user_id=evaluated[0],
        engine=engine,
    )
    temporal = validation._owned_temporal_recommendation_engine(
        engine,
        training_catalog,
        state,
    )

    assert isinstance(temporal._co, TemporalGraphSnapshot)
    marker = object()
    assert temporal._co.get(absent_seed, marker) is marker
    assert not temporal._co.materialized

    created = temporal._co[absent_seed]
    assert created == Counter()
    assert not temporal._co.materialized
    assert temporal._co[absent_seed] is created


def test_seed_counter_mutation_stays_slice_local_through_lazy_materialization() -> None:
    catalog, engine, evaluated = _fixture()
    timestamps, seeds = _target_rows(catalog, evaluated)
    states = validation._owned_temporal_states(
        catalog,
        timestamps[:2],
        seed_item_ids=seeds[:2],
    )
    engines = []
    legacy_engines = []
    for user_id, cutoff, state in zip(
        evaluated[:2],
        timestamps[:2],
        states,
        strict=True,
    ):
        training_catalog = validation._temporal_training_catalog(
            catalog,
            [event for event in catalog.interactions if event.timestamp < cutoff],
            user_id=user_id,
            engine=engine,
        )
        engines.append(
            validation._owned_temporal_recommendation_engine(
                engine,
                training_catalog,
                state,
            )
        )
        legacy_engines.append(
            validation._temporal_recommendation_engine(engine, training_catalog)
        )

    first_seed = next(
        seed_id
        for seed_id in seeds[0]
        if engines[0]._co.get(seed_id, None) is not None
    )
    first_counts = engines[0]._co.get(first_seed)
    assert first_counts is not None
    first_counts["synthetic-neighbor"] += 7

    assert "synthetic-neighbor" not in legacy_engines[0]._co.get(first_seed, {})
    assert "synthetic-neighbor" not in engines[1]._co.get(first_seed, {})

    dict(engines[0]._co)
    assert engines[0]._co[first_seed]["synthetic-neighbor"] == 7
    assert "synthetic-neighbor" not in legacy_engines[0]._co.get(first_seed, {})


def test_seed_graph_snapshots_preserve_full_relevance_report(monkeypatch) -> None:
    catalog, engine, evaluated = _fixture()
    optimized_builder = validation._owned_temporal_states

    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
    ).evaluate(engine.config)

    def full_graph_builder(
        current: Catalog,
        target_timestamps: list[float],
        *,
        seed_item_ids=None,
    ):
        del seed_item_ids
        return optimized_builder(current, target_timestamps)

    monkeypatch.setattr(validation, "_owned_temporal_states", full_graph_builder)
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
    ).evaluate(engine.config)
    monkeypatch.setattr(validation, "_owned_temporal_states", optimized_builder)

    assert optimized == legacy
