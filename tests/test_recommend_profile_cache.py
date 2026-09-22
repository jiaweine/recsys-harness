from __future__ import annotations

from lingjing_harness.algorithms.recommend_core import RecommendConfig, RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _engine(*, users: int = 1, history: int = 8) -> RecommendationEngine:
    items = [
        Item(
            item_id=f"item-{index:03d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 4}"],
            quality=(index % 10) / 10.0,
            freshness=((index * 3) % 10) / 10.0,
        )
        for index in range(max(32, users * history + 1))
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


def test_cached_profile_is_exact_and_mutation_isolated() -> None:
    engine = _engine(history=12)

    first = engine._profile("user-0")
    assert len(engine._profile_cache) == 1

    expected = (
        dict(first[0]),
        first[1].copy(),
        set(first[2]),
        first[3].copy(),
    )
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


def test_profile_cache_is_shared_by_config_clones_and_keyed_by_strategy() -> None:
    engine = _engine(history=10)

    default_profile = engine._profile("user-0")
    clone = engine.with_config(RecommendConfig(profile_strategy="recent_intent"))
    recent_profile = clone._profile("user-0")

    assert clone._profile_cache is engine._profile_cache
    assert len(engine._profile_cache) == 2
    assert engine._profile("user-0") == default_profile
    assert clone._profile("user-0") == recent_profile


def test_profile_cache_is_lru_bounded() -> None:
    engine = _engine(users=4, history=3)
    engine.MAX_PROFILE_CACHE = 2

    engine._profile("user-0")
    engine._profile("user-1")
    engine._profile("user-0")
    engine._profile("user-2")

    assert len(engine._profile_cache) == 2
    assert ("user-0", 30.0) in engine._profile_cache
    assert ("user-2", 30.0) in engine._profile_cache
    assert ("user-1", 30.0) not in engine._profile_cache
