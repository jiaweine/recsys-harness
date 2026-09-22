from __future__ import annotations

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.item_features import ItemVectorSnapshot
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(*, items: int = 640) -> tuple[RecommendationEngine, str]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 16}", f"cluster-{index % 29}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    vectors = ItemVectorSnapshot(
        {
            item.item_id: {
                (index * 7 + offset * 13) % 256: (offset + 1) / 64.0
                for offset in range(48)
            }
            for index, item in enumerate(rows)
        },
        dense_dims=256,
    )
    user_id = "warm-user"
    interactions = [
        Interaction(
            user_id=user_id,
            item_id=rows[index].item_id,
            event="click",
            weight=1.0 + (index % 5) * 0.1,
            timestamp=float(index + 1),
        )
        for index in range(64)
    ]
    return RecommendationEngine(
        Catalog(items=rows, interactions=interactions),
        item_vectors=vectors,
    ), user_id


def test_dense_profile_prepare_and_recommend_match_legacy_exactly() -> None:
    engine, user_id = _fixture()
    dims = engine._dense_vector_dims

    engine._dense_vector_dims = None
    legacy_prepared = engine.prepare(user_id)
    legacy_results = engine.recommend(user_id, limit=8)

    engine._dense_vector_dims = dims
    optimized_prepared = engine.prepare(user_id)
    optimized_results = engine.recommend(user_id, limit=8)

    assert optimized_prepared == legacy_prepared
    assert optimized_results == legacy_results


def test_dense_profile_metadata_is_shared_across_config_clones() -> None:
    engine, _ = _fixture()
    clone = engine.with_config(engine.config)

    assert engine._dense_vector_dims == 256
    assert clone._dense_vector_dims == engine._dense_vector_dims
    assert clone._vectors is engine._vectors


def test_plain_external_vectors_keep_legacy_cosine_path() -> None:
    items = [
        Item("seen", "Seen"),
        Item("candidate", "Candidate"),
    ]
    vectors = {
        "seen": {-5: 1.0, 10000: 0.5},
        "candidate": {-5: 0.8, 10000: 0.2},
    }
    engine = RecommendationEngine(
        Catalog(
            items=items,
            interactions=[
                Interaction(
                    user_id="external-user",
                    item_id="seen",
                    weight=1.0,
                    timestamp=1.0,
                )
            ],
        ),
        item_vectors=vectors,
    )

    assert engine._dense_vector_dims is None
    assert engine.prepare("external-user")
