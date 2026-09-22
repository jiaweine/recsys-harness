from __future__ import annotations

from heapq import nsmallest

import pytest

import lingjing_harness.algorithms.search as search_module
from lingjing_harness.algorithms.search import (
    SearchEngine,
    _candidate_postings_union,
    _candidate_semantic_rescue,
)
from lingjing_harness.algorithms.text import cosine
from lingjing_harness.domain import Catalog, Item


def _engine(*, items: int = 240, anchors: int = 12) -> tuple[SearchEngine, dict[int, float]]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Semantic Item {index}",
            categories=[f"cat-{index % 11}"],
            eligible=index % 17 != 0,
        )
        for index in range(items)
    ]
    vectors = {
        item.item_id: {
            (index * 7) % 64: 0.8,
            (index * 11 + 3) % 64: 0.6,
        }
        for index, item in enumerate(rows)
    }
    engine = SearchEngine(Catalog(items=rows), item_vectors=vectors)
    engine._postings["anchor"] = [
        item.item_id for item in engine.catalog.items[1 : anchors + 1]
    ]
    return engine, {7: 0.8, 19: 0.6, 37: 0.4}


def _legacy(
    engine: SearchEngine,
    qvec: dict[int, float],
) -> dict[str, str]:
    out = _candidate_postings_union(
        engine,
        "anchor",
        ["anchor"],
        ["anchor"],
        qvec,
    )
    if not out:
        return out
    semantic: list[tuple[float, str]] = []
    for item in engine.catalog.items:
        if not item.eligible or item.item_id in out:
            continue
        score = max(0.0, cosine(qvec, engine._vectors[item.item_id]))
        semantic.append((score, item.item_id))
    semantic.sort(key=lambda row: (-row[0], row[1]))
    budget = min(24, max(6, len(out)))
    for score, item_id in semantic[:budget]:
        if score >= 0.16:
            out[item_id] = "semantic"
    return out


@pytest.mark.parametrize("anchors", [1, 6, 12, 24])
def test_semantic_rescue_matches_full_sort_reference(anchors: int) -> None:
    engine, qvec = _engine(anchors=anchors)

    assert _candidate_semantic_rescue(
        engine,
        "anchor",
        ["anchor"],
        ["anchor"],
        qvec,
    ) == _legacy(engine, qvec)


def test_semantic_rescue_uses_bounded_topk(monkeypatch) -> None:
    engine, qvec = _engine(anchors=12)
    calls: list[int] = []

    def tracking_topk(n: int, iterable, *, key=None):
        calls.append(n)
        return nsmallest(n, iterable, key=key)

    monkeypatch.setattr(search_module, "nsmallest", tracking_topk)

    result = _candidate_semantic_rescue(
        engine,
        "anchor",
        ["anchor"],
        ["anchor"],
        qvec,
    )

    assert result
    assert calls == [12]


def test_semantic_rescue_preserves_threshold_and_ineligible_filter() -> None:
    items = [
        Item("anchor", "Anchor"),
        Item("eligible-hit", "Eligible Hit"),
        Item("eligible-low", "Eligible Low"),
        Item("ineligible-hit", "Ineligible Hit", eligible=False),
    ]
    vectors = {
        "anchor": {1: 1.0},
        "eligible-hit": {7: 1.0},
        "eligible-low": {9: 1.0},
        "ineligible-hit": {7: 1.0},
    }
    engine = SearchEngine(Catalog(items=items), item_vectors=vectors)
    engine._postings["anchor"] = ["anchor"]

    result = _candidate_semantic_rescue(
        engine,
        "anchor",
        ["anchor"],
        ["anchor"],
        {7: 1.0},
    )

    assert result["anchor"] == "lexical"
    assert result["eligible-hit"] == "semantic"
    assert "eligible-low" not in result
    assert "ineligible-hit" not in result


def test_semantic_rescue_keeps_item_id_tie_order() -> None:
    items = [Item("anchor", "Anchor")] + [
        Item(f"item-{index:02d}", f"Item {index}") for index in range(20)
    ]
    vectors = {item.item_id: {7: 1.0} for item in items}
    engine = SearchEngine(Catalog(items=items), item_vectors=vectors)
    engine._postings["anchor"] = ["anchor"]

    result = _candidate_semantic_rescue(
        engine,
        "anchor",
        ["anchor"],
        ["anchor"],
        {7: 1.0},
    )

    semantic_ids = [
        item_id for item_id, source in result.items() if source == "semantic"
    ]
    assert semantic_ids == [f"item-{index:02d}" for index in range(6)]
