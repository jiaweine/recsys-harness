from __future__ import annotations

import heapq
from heapq import nsmallest

import lingjing_harness.algorithms.recommend_core as recommend_core
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES, normalize_strategy_config
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.serving import normalize_serving_limit


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
    vectors = {
        item.item_id: {
            (index * 7) % 32: 0.8,
            (index * 11 + 3) % 32: 0.6,
        }
        for index, item in enumerate(catalog_items)
    }
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


def _legacy_rank_prepared(
    engine: RecommendationEngine,
    prepared: list[dict],
    *,
    limit: int = 10,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    cfg = normalize_strategy_config(engine.config)
    rows = []
    for raw in prepared:
        item = raw["item"]
        base = (
            cfg.profile * raw["profile_fit"]
            + cfg.graph * raw["graph"]
            + cfg.category * raw["cat_fit"]
            + cfg.quality * item.quality
            + cfg.freshness * item.freshness
            + cfg.popularity * raw["pop"]
            + cfg.novelty * raw["novelty"]
            + cfg.exploration * raw["explore"]
            + cfg.cold_start * raw.get("cold_prior", 0.0)
        )
        rows.append(
            {
                "item": item,
                "base": base,
                "signals": {
                    "fit": round(
                        min(
                            1.0,
                            0.55 * raw["profile_fit"]
                            + 0.30 * raw["cat_fit"]
                            + 0.15 * raw["graph"],
                        ),
                        4,
                    ),
                    "quality": round(item.quality, 4),
                    "freshness": round(item.freshness, 4),
                    "novelty": round(raw["novelty"], 4),
                },
            }
        )
    pool = nsmallest(
        max(40, limit * 6),
        rows,
        key=lambda row: (-row["base"], row["item"].item_id),
    )
    selected = []
    while pool and len(selected) < limit:
        best = None
        best_score = float("-inf")
        for row in pool:
            redundancy = max(
                (
                    CAPABILITIES.call(
                        "recommend.rerank",
                        cfg.rerank_strategy,
                        engine,
                        row["item"],
                        chosen["item"],
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            adjusted = row["base"] - cfg.diversity * redundancy
            if adjusted > best_score:
                best_score, best = adjusted, row
        assert best is not None
        selected.append({**best, "adjusted": best_score})
        pool.remove(best)
    return [
        {
            "rank": index + 1,
            **row["item"].public_dict(),
            "score": round(row["adjusted"], 5),
            "signals": row["signals"],
        }
        for index, row in enumerate(selected)
    ]


def test_rank_prepared_matches_pre_deferred_signals_output_exactly() -> None:
    engine = _engine()
    prepared = _prepared(engine)

    assert engine.rank_prepared(prepared, limit=8) == _legacy_rank_prepared(
        engine,
        prepared,
        limit=8,
    )


def test_rank_prepared_topk_matches_legacy_full_sort_exactly(monkeypatch) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    original = recommend_core.nsmallest

    monkeypatch.setattr(recommend_core, "nsmallest", _full_sort_topk)
    legacy = engine.rank_prepared(prepared, limit=8)

    monkeypatch.setattr(recommend_core, "nsmallest", original)
    optimized = engine.rank_prepared(prepared, limit=8)

    assert optimized == legacy


def test_rank_prepared_selects_raw_rows_before_building_signals(monkeypatch) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    calls: list[tuple[int, int, bool]] = []

    def tracking_topk(n: int, iterable, *, key=None):
        rows = list(iterable)
        calls.append((n, len(rows), bool(rows) and rows[0] is prepared[0]))
        return heapq.nsmallest(n, rows, key=key)

    monkeypatch.setattr(recommend_core, "nsmallest", tracking_topk)
    result = engine.rank_prepared(prepared, limit=8)

    assert result
    assert calls == [(48, len(prepared), True)]
