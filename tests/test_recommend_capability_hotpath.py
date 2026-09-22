from __future__ import annotations

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.domain import Catalog, Item


def _engine(*, items: int = 320) -> RecommendationEngine:
    return RecommendationEngine(
        Catalog(
            items=[
                Item(
                    item_id=f"item-{index:05d}",
                    title=f"Item {index}",
                    categories=[f"cat-{index % 13}"],
                    popularity=float(items - index),
                    quality=((index * 13) % 1000) / 1000.0,
                    freshness=((index * 29) % 1000) / 1000.0,
                )
                for index in range(items)
            ]
        ),
        item_vectors={
            f"item-{index:05d}": {}
            for index in range(items)
        },
    )


def _legacy_cold_prepare(engine: RecommendationEngine, user_id: str) -> list[dict]:
    profile, cats, seen, seeds = engine._profile(user_id)
    cat_total = sum(cats.values()) or 1.0
    graph_scores = engine._graph_scores(seeds)
    candidate_ids = CAPABILITIES.call(
        "recommend.candidate",
        engine.config.candidate_strategy,
        engine,
        user_id,
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )
    rows = []
    for item_id in dict.fromkeys(str(value) for value in candidate_ids):
        item = engine.catalog.item_by_id.get(item_id)
        if item is None or not item.eligible or item.item_id in seen:
            continue
        popularity = engine._popularity[item.item_id]
        explore = CAPABILITIES.call(
            "recommend.exploration",
            engine.config.exploration_strategy,
            engine,
            user_id,
            item,
            popularity,
        )
        cold_prior = CAPABILITIES.call(
            "recommend.cold_start",
            engine.config.cold_start_strategy,
            engine,
            item,
            popularity,
            explore,
        )
        rows.append(
            {
                "item": item,
                "profile_fit": 0.0,
                "cat_fit": 0.0 / cat_total,
                "graph": 0.0,
                "pop": popularity,
                "novelty": 1.0 - popularity,
                "explore": explore,
                "cold_prior": cold_prior,
            }
        )
    return rows


def test_bound_handlers_match_legacy_cold_prepare_and_slate_exactly() -> None:
    engine = _engine()
    user_id = "new-user"

    legacy_prepared = _legacy_cold_prepare(engine, user_id)
    optimized_prepared = engine.prepare(user_id)

    assert optimized_prepared == legacy_prepared
    assert engine.rank_prepared(optimized_prepared, limit=8) == engine.rank_prepared(
        legacy_prepared,
        limit=8,
    )


def test_exploration_and_cold_start_handlers_resolve_once_per_request(monkeypatch) -> None:
    engine = _engine(items=120)
    original = CAPABILITIES.resolve
    calls: list[str] = []

    def tracking_resolve(group, name):
        calls.append(group)
        return original(group, name)

    monkeypatch.setattr(CAPABILITIES, "resolve", tracking_resolve)
    result = engine.prepare("new-user")

    assert result
    assert calls.count("recommend.exploration") == 1
    assert calls.count("recommend.cold_start") == 1
