from __future__ import annotations

import math

from lingjing_harness.algorithms import RecommendationEngine, SearchEngine
from lingjing_harness.domain import Catalog, Item


def _catalog(size: int = 64) -> Catalog:
    return Catalog(
        items=[
            Item(
                item_id=f"item-{index}",
                title=f"Common Item {index}",
                text="common search item",
                categories=["common", f"group-{index % 8}"],
                popularity=float(index * index + 1),
                quality=0.8,
                freshness=0.7,
            )
            for index in range(size)
        ],
        name="popularity-test",
    )


def test_batch_popularity_snapshot_matches_single_item_contract():
    catalog = _catalog()

    expected = {
        item.item_id: catalog.popularity_norm(item)
        for item in catalog.items
    }

    assert catalog.popularity_norms() == expected


def test_search_prepare_reuses_precomputed_popularity(monkeypatch):
    catalog = _catalog()
    engine = SearchEngine(catalog)
    expected = dict(engine._popularity)  # noqa: SLF001 - performance contract

    def forbidden(_item):
        raise AssertionError("search prepare must not rescan catalog popularity")

    monkeypatch.setattr(catalog, "popularity_norm", forbidden)

    prepared = engine.prepare("common")

    assert len(prepared) == len(catalog.items)
    assert {
        row["item"].item_id: row["pop"]
        for row in prepared
    } == expected


def test_recommendation_engine_builds_popularity_without_single_item_rescans(monkeypatch):
    catalog = _catalog()

    def forbidden(_item):
        raise AssertionError("recommendation init must use batch popularity normalization")

    monkeypatch.setattr(catalog, "popularity_norm", forbidden)

    engine = RecommendationEngine(catalog)

    assert engine._popularity == catalog.popularity_norms()  # noqa: SLF001


def test_single_item_popularity_norm_remains_dynamic_after_batch_snapshot():
    catalog = _catalog(3)
    snapshot = catalog.popularity_norms()
    target = catalog.items[0]

    target.popularity = 1_000_000.0
    dynamic = catalog.popularity_norm(target)

    assert dynamic == 1.0
    assert snapshot[target.item_id] < dynamic
    assert math.isclose(
        catalog.popularity_norms()[target.item_id],
        dynamic,
        rel_tol=0.0,
        abs_tol=1e-12,
    )


def test_search_with_config_reuses_same_popularity_snapshot():
    catalog = _catalog()
    engine = SearchEngine(catalog)
    clone = engine.with_config(engine.config)

    assert clone._popularity is engine._popularity  # noqa: SLF001
