from __future__ import annotations

import heapq

import lingjing_harness.algorithms.recommend_core as recommend_core
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.domain import Catalog, Item


def _engine(*, items: int = 240) -> RecommendationEngine:
    catalog_items = [
        Item(
            item_id=f"item-{index:04d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 16}", f"cat-{(index * 7) % 16}"],
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    vectors = {item.item_id: {} for item in catalog_items}
    return RecommendationEngine(Catalog(items=catalog_items), item_vectors=vectors)


def _prepared(engine: RecommendationEngine) -> list[dict]:
    rows: list[dict] = []
    for index, item in enumerate(engine.catalog.items):
        popularity = ((index * 17) % 1000) / 1000.0
        rows.append(
            {
                "item": item,
                "profile_fit": ((index * 31) % 1000) / 1000.0,
                "cat_fit": ((index * 41) % 1000) / 1000.0,
                "graph": ((index * 37) % 1000) / 1000.0,
                "pop": popularity,
                "novelty": 1.0 - popularity,
                "explore": ((index * 43) % 1000) / 1000.0,
                "cold_prior": 0.0,
            }
        )
    return rows


def _full_sort_topk(n: int, iterable, *, key=None):
    return sorted(iterable, key=key)[:n]


def test_rank_prepared_topk_matches_legacy_full_sort_exactly(monkeypatch) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    original = recommend_core.nsmallest

    monkeypatch.setattr(recommend_core, "nsmallest", _full_sort_topk)
    legacy = engine.rank_prepared(prepared, limit=8)

    monkeypatch.setattr(recommend_core, "nsmallest", original)
    optimized = engine.rank_prepared(prepared, limit=8)

    assert optimized == legacy


def test_rank_prepared_bounds_sorting_to_mmr_pool(monkeypatch) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    calls: list[tuple[int, int]] = []

    def tracking_topk(n: int, iterable, *, key=None):
        rows = list(iterable)
        calls.append((n, len(rows)))
        return heapq.nsmallest(n, rows, key=key)

    monkeypatch.setattr(recommend_core, "nsmallest", tracking_topk)
    result = engine.rank_prepared(prepared, limit=8)

    assert result
    assert calls == [(48, len(prepared))]
