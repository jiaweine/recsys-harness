from __future__ import annotations

import lingjing_harness.algorithms.recommend_validation as validation
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(*, items: int = 96, users: int = 4) -> tuple[Catalog, RecommendationEngine]:
    rows = [
        Item(
            item_id=f"item-{index:04d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 7}"],
            popularity=float((index * 11) % 17),
            quality=((index * 13) % 10) / 10.0,
            freshness=((index * 7) % 10) / 10.0,
            eligible=index % 13 != 0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    for user_index in range(users):
        for offset in range(6):
            interactions.append(
                Interaction(
                    user_id=f"user-{user_index}",
                    item_id=rows[user_index * 8 + offset].item_id,
                    weight=1.0,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog)


def _legacy_popularity_rank(catalog: Catalog, seen: set[str], *, k: int) -> list[str]:
    candidates = [
        item
        for item in catalog.items
        if item.eligible and item.item_id not in seen
    ]
    popularity = catalog.popularity_norms()
    candidates.sort(
        key=lambda item: (
            -popularity[item.item_id],
            -item.quality,
            -item.freshness,
            item.item_id,
        )
    )
    return [item.item_id for item in candidates[:k]]


def test_precomputed_popularity_order_matches_legacy_filter_then_sort() -> None:
    catalog, _ = _fixture()
    ordered = validation._popularity_order(catalog)

    for seen in (
        set(),
        {"item-0001", "item-0002", "item-0017"},
        {f"item-{index:04d}" for index in range(40)},
    ):
        for k in (1, 5, 12, 40):
            assert validation._popularity_rank(
                catalog,
                seen,
                k=k,
                ordered=ordered,
            ) == _legacy_popularity_rank(catalog, seen, k=k)


def test_prepare_relevance_reuses_one_popularity_order(monkeypatch) -> None:
    catalog, engine = _fixture(users=5)
    original = validation._popularity_order
    calls = 0

    def counted(current: Catalog) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return original(current)

    monkeypatch.setattr(validation, "_popularity_order", counted)
    prepared = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    )

    assert len(prepared.slices) >= 3
    assert calls == 1


def test_precomputed_popularity_order_preserves_full_relevance_report(monkeypatch) -> None:
    catalog, engine = _fixture(users=5)
    optimized = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)

    def legacy_rank(
        current: Catalog,
        seen: set[str],
        *,
        k: int,
        ordered: tuple[str, ...] | None = None,
    ) -> list[str]:
        del ordered
        return _legacy_popularity_rank(current, seen, k=k)

    monkeypatch.setattr(validation, "_popularity_rank", legacy_rank)
    legacy = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
    ).evaluate(engine.config)

    assert optimized == legacy


def test_fully_cached_relevance_prepare_skips_popularity_order(monkeypatch) -> None:
    catalog, engine = _fixture(users=5)
    cache = validation.RecommendRelevanceSliceCache(catalog=catalog, engine=engine)
    first = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
        slice_cache=cache,
    )

    def fail_if_called(current: Catalog) -> tuple[str, ...]:
        raise AssertionError("fully cached relevance preparation should not rebuild popularity order")

    monkeypatch.setattr(validation, "_popularity_order", fail_if_called)
    second = validation.prepare_recommend_relevance(
        catalog,
        engine,
        users_override=engine.known_users(),
        k=8,
        slice_cache=cache,
    )

    assert second.slices == first.slices
