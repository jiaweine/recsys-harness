from __future__ import annotations

from dataclasses import replace
from heapq import nsmallest

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.search import SearchConfig
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.serving import normalize_serving_limit


def _engine_and_prepared(*, items: int = 320) -> tuple[SearchEngine, list[dict]]:
    catalog_items = [
        Item(
            item_id=f"item-{index:04d}",
            title=f"Item {index}",
            categories=[f"cat-{index % 17}", f"cluster-{index % 29}"],
            popularity=float((index * 17) % 1000),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
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
    engine = SearchEngine(Catalog(items=catalog_items), item_vectors=vectors)
    prepared = [
        {
            "item": item,
            "lex_raw": ((index * 37) % 1000) / 1000.0,
            "lex": ((index * 37) % 1000) / 1000.0,
            "sem": ((index * 19 + 7) % 1000) / 1000.0,
            "title": ((index * 23 + 5) % 1000) / 1000.0,
            "pop": engine._popularity[item.item_id],
            "candidate_source": "lexical",
        }
        for index, item in enumerate(catalog_items)
    ]
    return engine, prepared


def _legacy_rank_prepared(
    engine: SearchEngine,
    prepared: list[dict],
    *,
    config: SearchConfig | None = None,
    limit: int = 10,
) -> list[dict]:
    limit = normalize_serving_limit(limit)
    if limit == 0:
        return []
    cfg = config or engine.config
    rows: list[dict] = []
    for raw in prepared:
        item = raw["item"]
        base = (
            cfg.lexical * raw["lex"]
            + cfg.semantic * raw["sem"]
            + cfg.title * raw["title"]
            + cfg.quality * item.quality
            + cfg.popularity * raw["pop"]
            + cfg.freshness * item.freshness
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


def test_search_rank_matches_pre_deferred_signals_output_exactly() -> None:
    engine, prepared = _engine_and_prepared()
    for rerank_strategy in ("category_mmr", "semantic_mmr", "hybrid_mmr"):
        config = replace(engine.config, rerank_strategy=rerank_strategy)
        assert engine.rank_prepared(prepared, config=config, limit=8) == _legacy_rank_prepared(
            engine,
            prepared,
            config=config,
            limit=8,
        )


def test_search_rank_selects_raw_rows_before_building_signals(monkeypatch) -> None:
    engine, prepared = _engine_and_prepared()
    original = search_module.nsmallest
    calls: list[tuple[int, int, bool]] = []

    def tracking_topk(n: int, iterable, *, key=None):
        rows = list(iterable)
        calls.append((n, len(rows), bool(rows) and rows[0] is prepared[0]))
        return original(n, rows, key=key)

    monkeypatch.setattr(search_module, "nsmallest", tracking_topk)
    result = engine.rank_prepared(prepared, limit=8)

    assert result
    assert calls == [(48, len(prepared), True)]
