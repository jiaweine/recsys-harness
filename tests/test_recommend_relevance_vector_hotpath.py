from __future__ import annotations

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(*, items: int = 160, users: int = 4) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Temporal Item {index}",
            text="recommend relevance temporal validation",
            categories=[f"cat-{index % 11}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(users):
        user_id = f"user-{user_index:02d}"
        for offset in range(5):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[user_index * 8 + offset].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog)


def test_temporal_relevance_reuses_owned_item_vectors() -> None:
    catalog, engine = _fixture()
    prepared = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    )

    assert prepared.slices
    assert all(row.engine._vectors is engine._vectors for row in prepared.slices)


def test_shared_vectors_preserve_temporal_relevance_exactly(monkeypatch) -> None:
    catalog, engine = _fixture()
    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    )

    def legacy_engine(current, training_catalog):
        return RecommendationEngine(
            training_catalog,
            config=current.config,
        )

    monkeypatch.setattr(
        validation,
        "_temporal_recommendation_engine",
        legacy_engine,
    )
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    )

    assert [
        (row.user_id, row.target, row.target_timestamp, row.history)
        for row in optimized.slices
    ] == [
        (row.user_id, row.target, row.target_timestamp, row.history)
        for row in legacy.slices
    ]
    assert optimized.evaluate(engine.config) == legacy.evaluate(engine.config)
