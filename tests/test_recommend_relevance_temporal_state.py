from __future__ import annotations

from collections import Counter, defaultdict

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _state_fixture() -> tuple[Catalog, RecommendationEngine]:
    items = [
        Item(
            item_id=f"item-{index:04d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 13}"],
            popularity=float(index % 17),
            quality=((index * 7) % 10) / 10.0,
            freshness=((index * 3) % 10) / 10.0,
        )
        for index in range(240)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(4):
        user_id = f"user-{user_index:02d}"
        for offset in range(145):
            # Force both repeated items and more than MAX_GRAPH_HISTORY unique IDs.
            item_index = (user_index * 37 + offset) % 138
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=items[item_index].item_id,
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
        # Repeat older IDs to change recency order without changing the active set.
        for offset in range(8):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=items[(user_index * 37 + offset * 5) % 138].item_id,
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=items, interactions=interactions)
    return catalog, RecommendationEngine(catalog)


def test_incremental_temporal_states_match_constructor_exactly() -> None:
    catalog, engine = _state_fixture()
    cutoffs = [80.5, 220.5, 410.5, 590.5]
    states = validation._owned_temporal_states(catalog, cutoffs)

    assert len(states) == len(cutoffs)
    for cutoff, (by_user, co) in zip(cutoffs, states, strict=True):
        training_interactions = [
            event
            for event in catalog.interactions
            if event.timestamp < cutoff
        ]
        training_catalog = validation._temporal_training_catalog(
            catalog,
            training_interactions,
            user_id="state-check",
            engine=engine,
        )
        legacy = validation._temporal_recommendation_engine(
            engine,
            training_catalog,
        )

        assert dict(by_user) == dict(legacy._by_user)
        assert {
            item_id: Counter(counts)
            for item_id, counts in co.items()
        } == {
            item_id: Counter(counts)
            for item_id, counts in legacy._co.items()
        }


def _relevance_fixture(
    *,
    items_count: int = 1200,
    background_users: int = 12,
    evaluated_users: int = 5,
    history: int = 24,
) -> tuple[Catalog, RecommendationEngine, list[str]]:
    items = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Relevance Item {index}",
            text="temporal state exactness",
            categories=[f"cat-{index % 19}", f"cluster-{index % 29}"],
            popularity=float((items_count - index) % 101),
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
            item_index = (seed * 17 + offset * 3) % items_count
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=items[item_index].item_id,
                    weight=1.0,
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


def test_incremental_temporal_states_preserve_full_relevance_report(monkeypatch) -> None:
    catalog, engine, evaluated = _relevance_fixture()
    optimized_materializer = validation._owned_temporal_recommendation_engine

    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
    ).evaluate(engine.config)

    def legacy_materializer(
        current: RecommendationEngine,
        training_catalog: Catalog,
        state,
    ) -> RecommendationEngine:
        del state
        return validation._temporal_recommendation_engine(
            current,
            training_catalog,
        )

    monkeypatch.setattr(
        validation,
        "_owned_temporal_recommendation_engine",
        legacy_materializer,
    )
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
    ).evaluate(engine.config)
    monkeypatch.setattr(
        validation,
        "_owned_temporal_recommendation_engine",
        optimized_materializer,
    )

    assert optimized == legacy


def test_single_pending_slice_keeps_simple_constructor_path(monkeypatch) -> None:
    catalog, engine, evaluated = _relevance_fixture(evaluated_users=2)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("one pending slice should not build incremental state")

    monkeypatch.setattr(validation, "_owned_temporal_states", fail_if_called)
    prepared = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=[evaluated[0]],
        k=8,
    )

    assert len(prepared.slices) == 1


def test_fully_cached_prepare_skips_incremental_state(monkeypatch) -> None:
    catalog, engine, evaluated = _relevance_fixture()
    cache = validation.RecommendRelevanceSliceCache(catalog=catalog, engine=engine)
    first = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
        slice_cache=cache,
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("fully cached preparation should not rebuild temporal state")

    monkeypatch.setattr(validation, "_owned_temporal_states", fail_if_called)
    second = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=evaluated,
        k=8,
        slice_cache=cache,
    )

    assert second.slices == first.slices
