from __future__ import annotations

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item, QueryLabel


def _fixture(*, items: int = 360, users: int = 6) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 11}", f"cluster-{index % 17}"],
            popularity=float((items - index) % 97),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
            eligible=index % 23 != 0,
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
                    item_id=rows[user_index * 9 + offset + 1].item_id,
                    event="click",
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(
        items=rows,
        interactions=interactions,
        query_labels=[
            QueryLabel("sample query", ["item-00001", "item-00002"]),
        ],
        name="validated-source",
    )
    return catalog, RecommendationEngine(catalog)


def _legacy_catalog(
    catalog: Catalog,
    training_interactions: list[Interaction],
    *,
    user_id: str,
    engine: object,
) -> Catalog:
    del engine
    return Catalog(
        items=list(catalog.items),
        interactions=training_interactions,
        query_labels=list(catalog.query_labels),
        events=[],
        reward_spec=None,
        name=f"{catalog.name}:temporal-relevance:{user_id}",
    )


def test_owned_temporal_catalog_matches_full_catalog_normalization() -> None:
    catalog, engine = _fixture()
    training_interactions = [
        event for event in catalog.interactions if event.timestamp < 20.0
    ]

    optimized = validation._temporal_training_catalog(
        catalog,
        training_interactions,
        user_id="user-03",
        engine=engine,
    )
    legacy = _legacy_catalog(
        catalog,
        training_interactions,
        user_id="user-03",
        engine=engine,
    )

    assert optimized.to_payload() == legacy.to_payload()
    assert optimized.items is not catalog.items
    assert optimized.item_by_id is not catalog.item_by_id
    assert optimized.query_labels is not catalog.query_labels
    assert optimized.interactions is training_interactions


def test_external_temporal_catalog_keeps_full_normalization_path() -> None:
    catalog, _ = _fixture()
    training_interactions = list(catalog.interactions[:12])

    class ExternalRuntime:
        pass

    optimized = validation._temporal_training_catalog(
        catalog,
        training_interactions,
        user_id="external-user",
        engine=ExternalRuntime(),
    )
    legacy = _legacy_catalog(
        catalog,
        training_interactions,
        user_id="external-user",
        engine=ExternalRuntime(),
    )

    assert optimized.to_payload() == legacy.to_payload()
    assert optimized.interactions is not training_interactions


def test_validated_catalog_snapshot_preserves_full_relevance_report(monkeypatch) -> None:
    catalog, engine = _fixture()
    optimized_helper = validation._temporal_training_catalog

    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)

    monkeypatch.setattr(validation, "_temporal_training_catalog", _legacy_catalog)
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)
    monkeypatch.setattr(
        validation,
        "_temporal_training_catalog",
        optimized_helper,
    )

    assert optimized == legacy
