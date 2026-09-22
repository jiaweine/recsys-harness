from __future__ import annotations

from lingjing_harness.algorithms.recommend_core import RecommendConfig, RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _engine(*, users: int = 1, history: int = 8) -> RecommendationEngine:
    items = [
        Item(
            item_id=f"item-{index:03d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 5}", f"cluster-{index % 7}"],
            popularity=float(100 - index),
            quality=(index % 10) / 10.0,
            freshness=((index * 3) % 10) / 10.0,
        )
        for index in range(max(48, users * history + 8))
    ]
    interactions = [
        Interaction(
            user_id=f"user-{user}",
            item_id=items[user * history + offset].item_id,
            event="click",
            weight=1.0 + 0.1 * (offset % 3),
            timestamp=float(offset + 1),
        )
        for user in range(users)
        for offset in range(history)
    ]
    return RecommendationEngine(Catalog(items=items, interactions=interactions))


def _without_snapshot(engine: RecommendationEngine):
    original = engine._owned_profile_snapshot
    engine._owned_profile_snapshot = lambda user_id: None
    return original


def test_owned_profile_snapshot_preserves_prepare_and_recommend_exactly() -> None:
    base = _engine(history=12)

    for profile_strategy in ("recency_balanced", "recent_intent", "long_horizon"):
        for candidate_strategy in ("full_pool", "evidence_union"):
            engine = base.with_config(
                RecommendConfig(
                    profile_strategy=profile_strategy,
                    candidate_strategy=candidate_strategy,
                )
            )
            original = _without_snapshot(engine)
            legacy_prepared = engine.prepare("user-0")
            legacy_results = engine.recommend("user-0", limit=8)
            engine._owned_profile_snapshot = original

            optimized_prepared = engine.prepare("user-0")
            optimized_results = engine.recommend("user-0", limit=8)

            assert optimized_prepared == legacy_prepared
            assert optimized_results == legacy_results


def test_public_profile_calls_keep_fresh_mutable_objects() -> None:
    engine = _engine(history=10)

    first = engine._profile("user-0")
    expected = (dict(first[0]), first[1].copy(), set(first[2]), first[3].copy())
    first[0].clear()
    first[1].clear()
    first[2].clear()
    first[3].clear()

    second = engine._profile("user-0")

    assert second == expected
    assert second[0] is not first[0]
    assert second[1] is not first[1]
    assert second[2] is not first[2]
    assert second[3] is not first[3]
    assert not engine._profile_snapshot_cache


def test_profile_snapshot_cache_is_shared_and_lru_bounded() -> None:
    engine = _engine(users=4, history=3)
    engine.MAX_PROFILE_SNAPSHOTS = 2

    engine.prepare("user-0")
    clone = engine.with_config(RecommendConfig(profile_strategy="recent_intent"))
    clone.prepare("user-0")
    assert clone._profile_snapshot_cache is engine._profile_snapshot_cache

    engine.prepare("user-1")
    engine.prepare("user-2")

    assert len(engine._profile_snapshot_cache) == 2
    assert ("user-1", 30.0) in engine._profile_snapshot_cache
    assert ("user-2", 30.0) in engine._profile_snapshot_cache


def test_cold_users_do_not_fill_profile_snapshot_cache() -> None:
    engine = _engine(history=5)

    engine.prepare("brand-new-user")

    assert not engine._profile_snapshot_cache
