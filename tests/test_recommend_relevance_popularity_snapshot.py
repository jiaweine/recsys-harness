from __future__ import annotations

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(*, items: int = 320, users: int = 6) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 11}", f"cluster-{index % 17}"],
            popularity=float((items - index) % 97),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(users):
        for offset in range(6):
            interactions.append(
                Interaction(
                    user_id=f"user-{user_index:02d}",
                    item_id=rows[user_index * 9 + offset].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog)


def test_temporal_relevance_engines_share_popularity_snapshot() -> None:
    catalog, engine = _fixture()

    prepared = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    )

    assert prepared.slices
    assert all(row.engine._popularity is engine._popularity for row in prepared.slices)


def test_shared_popularity_snapshot_preserves_full_relevance_report(monkeypatch) -> None:
    catalog, engine = _fixture()
    optimized_helper = validation._temporal_recommendation_engine

    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)

    def legacy_engine(
        current: RecommendationEngine,
        training_catalog: Catalog,
    ) -> RecommendationEngine:
        return RecommendationEngine(
            training_catalog,
            config=current.config,
            item_vectors=current._vectors,
        )

    monkeypatch.setattr(validation, "_temporal_recommendation_engine", legacy_engine)
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)
    monkeypatch.setattr(
        validation,
        "_temporal_recommendation_engine",
        optimized_helper,
    )

    assert optimized == legacy


def test_explicit_popularity_snapshot_keeps_normal_constructor_semantics() -> None:
    catalog, engine = _fixture(items=120, users=3)
    copied = dict(engine._popularity)

    rebuilt = RecommendationEngine(
        catalog,
        item_vectors=engine._vectors,
        popularity_norms=copied,
    )

    assert rebuilt._popularity is copied
    assert rebuilt.recommend("user-00", limit=8) == engine.recommend("user-00", limit=8)
