from __future__ import annotations

from dataclasses import replace
import heapq

import pytest

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.search import SearchConfig
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.serving import normalize_serving_limit


def _engine(*, items: int = 320) -> SearchEngine:
    catalog_items = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Search Item {index}",
            categories=[f"cat-{index % 13}", f"cluster-{index % 29}"],
            popularity=float((index * 17) % 1000),
            quality=((index * 13) % 100) / 100.0,
            freshness=((index * 29) % 100) / 100.0,
        )
        for index in range(items)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 64: 0.8,
            (index * 11 + 3) % 64: 0.6,
        }
        for index, item in enumerate(catalog_items)
    }
    return SearchEngine(Catalog(items=catalog_items), item_vectors=vectors)


def _prepared(engine: SearchEngine) -> list[dict]:
    return [
        {
            "item": item,
            "lex_raw": ((index * 37) % 1000) / 1000.0,
            "lex": ((index * 37) % 1000) / 1000.0,
            "sem": ((index * 19 + 7) % 1000) / 1000.0,
            "title": ((index * 23 + 5) % 1000) / 1000.0,
            "pop": engine._popularity[item.item_id],
            "candidate_source": "lexical",
        }
        for index, item in enumerate(engine.catalog.items)
    ]


def _legacy_rank_prepared(
    engine: SearchEngine,
    prepared: list[dict],
    *,
    config: SearchConfig,
    limit: int,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    rows: list[dict] = []
    for raw in prepared:
        item = raw["item"]
        base = (
            config.lexical * raw["lex"]
            + config.semantic * raw["sem"]
            + config.title * raw["title"]
            + config.quality * item.quality
            + config.popularity * raw["pop"]
            + config.freshness * item.freshness
        )
        rows.append(
            {
                **raw,
                "base": base,
                "signals": {
                    "match": round(0.65 * raw["lex"] + 0.35 * raw["sem"], 4),
                    "quality": round(item.quality, 4),
                    "freshness": round(item.freshness, 4),
                    "popularity": round(raw["pop"], 4),
                },
            }
        )
    rows.sort(key=lambda row: (-row["base"], row["item"].item_id))
    pool = rows[: max(30, limit * 6)]
    selected: list[dict] = []
    while pool and len(selected) < limit:
        best = None
        best_score = float("-inf")
        for row in pool:
            redundancy = max(
                (
                    CAPABILITIES.call(
                        "search.rerank",
                        config.rerank_strategy,
                        engine,
                        row["item"],
                        chosen["item"],
                    )
                    for chosen in selected
                ),
                default=0.0,
            )
            adjusted = row["base"] - config.diversity * redundancy
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


@pytest.mark.parametrize(
    "rerank_strategy",
    ["category_mmr", "semantic_mmr", "hybrid_mmr"],
)
def test_rank_prepared_matches_pre_deferred_signal_output_exactly(
    rerank_strategy: str,
) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    config = replace(
        engine.config,
        rerank_strategy=rerank_strategy,
        lexical=0.31,
        semantic=0.29,
        title=0.17,
        quality=0.08,
        popularity=0.06,
        freshness=0.09,
        diversity=0.13,
    )

    assert engine.rank_prepared(
        prepared,
        config=config,
        limit=8,
    ) == _legacy_rank_prepared(
        engine,
        prepared,
        config=config,
        limit=8,
    )


def test_rank_prepared_selects_raw_rows_before_signal_materialization(monkeypatch) -> None:
    engine = _engine()
    prepared = _prepared(engine)
    calls: list[tuple[int, int, bool]] = []

    def tracking_topk(n: int, iterable, *, key=None):
        rows = list(iterable)
        calls.append((n, len(rows), bool(rows) and rows[0] is prepared[0]))
        return heapq.nsmallest(n, rows, key=key)

    monkeypatch.setattr(search_module, "nsmallest", tracking_topk)

    result = engine.rank_prepared(prepared, limit=8)

    assert result
    assert calls == [(48, len(prepared), True)]


def test_rank_prepared_preserves_tie_breaking_by_item_id() -> None:
    engine = _engine(items=80)
    prepared = _prepared(engine)
    for raw in prepared:
        raw["lex"] = 0.5
        raw["sem"] = 0.5
        raw["title"] = 0.5
        raw["pop"] = 0.5
        raw["item"].quality = 0.5
        raw["item"].freshness = 0.5

    config = replace(engine.config, diversity=0.0)
    result = engine.rank_prepared(prepared, config=config, limit=8)

    assert [row["id"] for row in result] == sorted(
        raw["item"].item_id for raw in prepared
    )[:8]
