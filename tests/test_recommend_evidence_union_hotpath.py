from __future__ import annotations

from collections import Counter

from lingjing_harness.algorithms.recommend_core import (
    RecommendConfig,
    RecommendationEngine,
    _candidate_evidence_union,
)
from lingjing_harness.algorithms.text import cosine
from lingjing_harness.domain import Catalog, Interaction, Item


def _engine(*, items: int = 240, history: int = 24) -> RecommendationEngine:
    catalog_items = [
        Item(
            item_id=f"item-{index:04d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 12}"],
            popularity=float((index * 17) % 101),
            quality=((index * 13) % 100) / 100.0,
            freshness=((index * 29) % 100) / 100.0,
            eligible=index % 11 != 0,
        )
        for index in range(items)
    ]
    interactions = [
        Interaction(
            user_id="warm-user",
            item_id=f"item-{(index * 13) % items:04d}",
            timestamp=float(index),
        )
        for index in range(history)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 32: 0.8,
            (index * 11 + 3) % 32: 0.6,
        }
        for index, item in enumerate(catalog_items)
    }
    return RecommendationEngine(
        Catalog(items=catalog_items, interactions=interactions),
        RecommendConfig(candidate_strategy="evidence_union"),
        item_vectors=vectors,
    )


def _legacy_candidate_evidence_union(
    engine: RecommendationEngine,
    user_id: str,
    profile: dict[int, float],
    cats: Counter[str],
    seen: set[str],
    seeds: Counter[str],
    graph_scores: dict[str, float],
) -> list[str]:
    eligible = [
        item
        for item in engine.catalog.items
        if item.eligible and item.item_id not in seen
    ]
    if not seeds or not eligible:
        return [item.item_id for item in eligible]

    selected: set[str] = set(graph_scores)
    if cats:
        category_keys = set(cats)
        for item in eligible:
            if set(item.categories) & category_keys:
                selected.add(item.item_id)

    semantic = []
    if profile:
        for item in eligible:
            semantic.append(
                (max(0.0, cosine(profile, engine._vectors[item.item_id])), item.item_id)
            )
        semantic.sort(key=lambda row: (-row[0], row[1]))
        selected.update(item_id for _, item_id in semantic[:24])

    target = min(len(eligible), max(24, int(len(eligible) * 0.55)))
    if len(selected) < target:
        fallback = sorted(
            eligible,
            key=lambda item: (
                -(
                    0.45 * item.quality
                    + 0.35 * item.freshness
                    + 0.20 * engine._popularity[item.item_id]
                ),
                item.item_id,
            ),
        )
        for item in fallback:
            selected.add(item.item_id)
            if len(selected) >= target:
                break
    return sorted(selected)


def test_evidence_union_candidate_selection_matches_legacy_exactly() -> None:
    engine = _engine()
    profile, cats, seen, seeds = engine._profile("warm-user")
    graph_scores = engine._graph_scores(seeds)

    legacy = _legacy_candidate_evidence_union(
        engine,
        "warm-user",
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )
    optimized = _candidate_evidence_union(
        engine,
        "warm-user",
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )

    assert optimized == legacy
    assert "evidence_fallback_ids" in engine._candidate_static_cache


def test_evidence_union_fallback_cache_is_shared_by_config_clones() -> None:
    engine = _engine()
    profile, cats, seen, seeds = engine._profile("warm-user")
    graph_scores = engine._graph_scores(seeds)
    _candidate_evidence_union(
        engine,
        "warm-user",
        profile,
        cats,
        seen,
        seeds,
        graph_scores,
    )

    clone = engine.with_config(
        RecommendConfig(
            candidate_strategy="evidence_union",
            rerank_strategy="semantic_mmr",
        )
    )

    assert clone._candidate_static_cache is engine._candidate_static_cache
    assert (
        clone._candidate_static_cache["evidence_fallback_ids"]
        is engine._candidate_static_cache["evidence_fallback_ids"]
    )
