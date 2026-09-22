from __future__ import annotations

import struct

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms import SearchEngine
from lingjing_harness.algorithms.text import cosine, hashed_vector
from lingjing_harness.domain import Catalog, Item


def _bits(value: float) -> bytes:
    return struct.pack("!d", value)


def _engine(*, items: int = 640) -> SearchEngine:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Wireless Headphones {index}",
            text="wireless bluetooth audio headphones travel music",
            categories=["audio", "headphones", f"series-{index % 12}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    return SearchEngine(Catalog(items=rows))


def test_query_cosine_matches_legacy_bit_for_bit() -> None:
    vectors = [
        ({1: 0.5}, {1: 0.25, 2: 0.75}),
        ({1: 0.5, 2: -0.25}, {1: 0.25, 2: 0.75}),
        ({1: 0.5, 2: -0.25, 3: 0.75}, {1: 0.25, 2: 0.75}),
        (
            hashed_vector("wireless headphones"),
            hashed_vector("wireless bluetooth audio headphones travel music"),
        ),
    ]
    for qvec, item_vector in vectors:
        optimized = search_module._query_cosine(
            qvec,
            tuple(qvec.items()),
            item_vector,
        )
        legacy = cosine(qvec, item_vector)
        assert _bits(optimized) == _bits(legacy)


def test_prepare_and_search_match_legacy_query_cosine_exactly(monkeypatch) -> None:
    engine = _engine()
    query = "wireless headphones"
    original = search_module._query_cosine

    monkeypatch.setattr(
        search_module,
        "_query_cosine",
        lambda qvec, qitems, item_vector: cosine(qvec, item_vector),
    )
    legacy_prepared = engine.prepare(query)
    legacy_results = engine.search(query, limit=8)

    monkeypatch.setattr(search_module, "_query_cosine", original)
    optimized_prepared = engine.prepare(query)
    optimized_results = engine.search(query, limit=8)

    assert optimized_prepared == legacy_prepared
    assert optimized_results == legacy_results
