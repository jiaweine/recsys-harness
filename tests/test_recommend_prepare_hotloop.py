from __future__ import annotations

from collections import Counter

import pytest

from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.item_features import ItemVectorSnapshot
from lingjing_harness.algorithms.recommend_core import RecommendConfig, RecommendationEngine
from lingjing_harness.algorithms.text import cosine
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(
    config: RecommendConfig,
    *,
    warm: bool,
    items: int = 480,
) -> tuple[RecommendationEngine, str]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 16}", f"cluster-{index % 29}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
            eligible=index % 19 != 0,
        )
        for index in range(items)
    ]
    vectors = ItemVectorSnapshot(
        {
            item.item_id: {
                (index * 7 + offset * 13) % 256: (offset + 1) / 64.0
                for offset in range(32)
            }
            for index, item in enumerate(rows)
        },
        dense_dims=256,
    )
    user_id = "warm-user" if warm else "cold-user"
    interactions = (
        [
            Interaction(
                user_id=user_id,
                item_id=rows[index].item_id,
                event="click",
                weight=1.0 + (index % 5) * 0.1,
                timestamp=float(index + 1),
            )
            for index in range(72)
        ]
        if warm
        else []
    )
    return (
        RecommendationEngine(
            Catalog(items=rows, interactions=interactions),
            config,
            item_vectors=vectors,
        ),
        user_id,
    )


def _legacy_prepare(engine: RecommendationEngine, user_id: str) -> list[dict]:
    profile, cats, seen, seeds = engine._profile(user_id)
    dense_profile = engine._dense_profile(profile)
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
    cold = len(engine._by_user.get(user_id, [])) == 0
    rows = []
    for item_id in dict.fromkeys(str(value) for value in candidate_ids):
        item = engine.catalog.item_by_id.get(item_id)
        if item is None or not item.eligible or item.item_id in seen:
            continue
        item_vector = engine._vectors[item.item_id]
        if not profile:
            profile_fit = 0.0
        elif dense_profile is not None and len(profile) > len(item_vector):
            profile_fit = max(
                0.0,
                sum(
                    value * dense_profile[key]
                    for key, value in item_vector.items()
                ),
            )
        else:
            profile_fit = max(0.0, cosine(profile, item_vector))
        cat_fit = sum(cats.get(category, 0.0) for category in item.categories) / cat_total
        graph = graph_scores.get(item.item_id, 0.0)
        popularity = engine._popularity[item.item_id]
        novelty = 1.0 - popularity
        explore = CAPABILITIES.call(
            "recommend.exploration",
            engine.config.exploration_strategy,
            engine,
            user_id,
            item,
            popularity,
        )
        cold_prior = (
            CAPABILITIES.call(
                "recommend.cold_start",
                engine.config.cold_start_strategy,
                engine,
                item,
                popularity,
                explore,
            )
            if cold
            else 0.0
        )
        rows.append(
            {
                "item": item,
                "profile_fit": profile_fit,
                "cat_fit": cat_fit,
                "graph": graph,
                "pop": popularity,
                "novelty": novelty,
                "explore": explore,
                "cold_prior": cold_prior,
            }
        )
    return rows


@pytest.mark.parametrize(
    ("config", "warm"),
    [
        (RecommendConfig(), True),
        (
            RecommendConfig(
                candidate_strategy="evidence_union",
                exploration_strategy="novelty_seek",
            ),
            True,
        ),
        (
            RecommendConfig(
                exploration_strategy="coverage_seek",
                cold_start_strategy="discovery_prior",
            ),
            False,
        ),
        (
            RecommendConfig(
                candidate_strategy="evidence_union",
                exploration_strategy="coverage_seek",
                cold_start_strategy="fresh_explore",
            ),
            False,
        ),
    ],
)
def test_prepare_hot_loop_matches_legacy_exactly(
    config: RecommendConfig,
    warm: bool,
) -> None:
    engine, user_id = _fixture(config, warm=warm)

    legacy = _legacy_prepare(engine, user_id)
    optimized = engine.prepare(user_id)

    assert optimized == legacy
    assert engine.rank_prepared(optimized, limit=8) == engine.rank_prepared(
        legacy,
        limit=8,
    )


def test_plain_external_vectors_keep_exact_legacy_prepare_semantics() -> None:
    items = [
        Item("seen", "Seen", categories=["a"]),
        Item("candidate-a", "Candidate A", categories=["a"]),
        Item("candidate-b", "Candidate B", categories=["b"]),
    ]
    vectors = {
        "seen": {-5: 1.0, 10000: 0.5},
        "candidate-a": {-5: 0.8, 10000: 0.2},
        "candidate-b": {-5: 0.1, 10000: 0.9},
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
    assert engine.prepare("external-user") == _legacy_prepare(
        engine,
        "external-user",
    )
