from __future__ import annotations

from collections import Counter

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.algorithms.text import tokenize


def _engine(*, items: int = 320) -> SearchEngine:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Wireless Headphones {index}",
            text="wireless bluetooth audio headphones travel music",
            categories=["audio", "headphones", f"series-{index % 12}"],
            popularity=float(items - index),
            quality=0.55 + 0.4 * ((index % 17) / 16.0),
            freshness=0.45 + 0.5 * ((index % 19) / 18.0),
        )
        for index in range(items)
    ]
    return SearchEngine(Catalog(items=rows))


def _legacy_bm25(
    engine: SearchEngine,
    item: Item,
    qtokens: list[str],
    query_weights: dict[str, float] | None = None,
) -> float:
    del query_weights
    toks = engine._doc_tokens[item.item_id]
    title_tokens, text_tokens, category_tokens = engine._field_tokens[item.item_id]
    title_tf = Counter(title_tokens)
    text_tf = Counter(text_tokens)
    category_tf = Counter(category_tokens)
    dl = max(1, len(toks))
    score = 0.0
    k1, b = 1.45, 0.72
    for token in qtokens:
        f = (
            2.1 * title_tf.get(token, 0)
            + text_tf.get(token, 0)
            + 0.75 * category_tf.get(token, 0)
        )
        if f <= 0:
            continue
        query_weight = 0.45 if token in engine.GENERIC_QUERY_TOKENS else 1.0
        score += query_weight * engine._idf(token) * (f * (k1 + 1)) / (
            f + k1 * (1 - b + b * dl / max(1.0, engine._avg_len))
        )
    return score


def test_cached_bm25_matches_legacy_exactly() -> None:
    engine = _engine()
    for query in ("wireless headphones", "audio travel", "商品 headphones"):
        qtokens = list(dict.fromkeys(tokenize(query)))
        query_weights = {
            token: (
                (0.45 if token in engine.GENERIC_QUERY_TOKENS else 1.0)
                * engine._idf(token)
            )
            for token in qtokens
        }
        for item in engine.catalog.items:
            assert engine._bm25(
                item,
                qtokens,
                query_weights,
            ) == _legacy_bm25(engine, item, qtokens)


def test_search_output_matches_legacy_bm25_exactly(monkeypatch) -> None:
    engine = _engine(items=600)
    query = "wireless headphones"
    original = SearchEngine._bm25

    monkeypatch.setattr(
        SearchEngine,
        "_bm25",
        lambda self, item, qtokens, query_weights=None: _legacy_bm25(
            self,
            item,
            qtokens,
            query_weights,
        ),
    )
    legacy = engine.search(query, limit=8)

    monkeypatch.setattr(SearchEngine, "_bm25", original)
    optimized = engine.search(query, limit=8)

    assert optimized == legacy


def test_search_config_clones_share_bm25_static_cache() -> None:
    engine = _engine()
    clone = engine.with_config(engine.config)

    assert clone._bm25_tf is engine._bm25_tf
    assert clone._bm25_length_norm is engine._bm25_length_norm


def test_bm25_hot_path_does_not_construct_field_counters(monkeypatch) -> None:
    engine = _engine()
    item = engine.catalog.items[0]
    qtokens = list(dict.fromkeys(tokenize("wireless headphones")))
    query_weights = {
        token: engine._idf(token)
        for token in qtokens
    }

    def fail_counter(*args, **kwargs):
        raise AssertionError("BM25 hot path rebuilt static field counters")

    monkeypatch.setattr(search_module, "Counter", fail_counter)

    assert engine._bm25(item, qtokens, query_weights) > 0
