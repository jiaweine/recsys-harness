from __future__ import annotations

from hashlib import blake2b

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.domain import Catalog, Item


def _engine(*, items: int = 480) -> RecommendationEngine:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 17}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    return RecommendationEngine(
        Catalog(items=rows),
        item_vectors={item.item_id: {} for item in rows},
    )


def _legacy_stable_hash(user_id: str, item_id: str) -> float:
    value = int.from_bytes(
        blake2b(f"{user_id}:{item_id}".encode(), digest_size=4).digest(),
        "little",
    )
    return (value % 1000) / 1000.0


def _legacy_full_pool_prepare(
    engine: RecommendationEngine,
    user_id: str,
) -> list[dict]:
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
        explore = _legacy_stable_hash(user_id, item.item_id) * item.freshness
        cold_prior = (
            0.45 * item.quality
            + 0.35 * item.freshness
            + 0.20 * popularity
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


def test_stable_hash_prefix_template_matches_legacy_exactly() -> None:
    RecommendationEngine._stable_hash_template.cache_clear()
    for user_id in ("new-user", "u:1", "用户-42"):
        for item_id in ("item-1", "item:2", "内容-3"):
            assert RecommendationEngine._stable_hash(
                user_id,
                item_id,
            ) == _legacy_stable_hash(user_id, item_id)


def test_streamed_full_pool_matches_legacy_cold_prepare_and_slate_exactly() -> None:
    engine = _engine()
    user_id = "new-user"

    legacy_prepared = _legacy_full_pool_prepare(engine, user_id)
    optimized_prepared = engine.prepare(user_id)

    assert optimized_prepared == legacy_prepared
    assert engine.rank_prepared(optimized_prepared, limit=8) == engine.rank_prepared(
        legacy_prepared,
        limit=8,
    )


def test_non_full_pool_candidate_strategy_preserves_generic_path() -> None:
    engine = _engine()
    configured = engine.with_config(
        type(engine.config)(candidate_strategy="evidence_union")
    )

    assert configured.prepare("new-user")
