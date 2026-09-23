from __future__ import annotations

from collections import Counter

from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.recommend_temporal_graph import (
    SeedGraphSnapshot,
    TemporalGraphSnapshot,
)
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture() -> tuple[Catalog, RecommendationEngine]:
    items = [
        Item(item_id=f"item-{index}", title=f"Item {index}")
        for index in range(6)
    ]
    catalog = Catalog(
        items=items,
        interactions=[
            Interaction("u1", "item-0", timestamp=1.0),
            Interaction("u1", "item-1", timestamp=2.0),
            Interaction("u1", "item-2", timestamp=3.0),
            Interaction("u2", "item-1", timestamp=1.0),
            Interaction("u2", "item-3", timestamp=2.0),
        ],
    )
    return catalog, RecommendationEngine(catalog)


def _graph() -> tuple[TemporalGraphSnapshot, RecommendationEngine]:
    catalog, engine = _fixture()
    snapshot = SeedGraphSnapshot(
        {"item-1": Counter(engine._co["item-1"])},
        {"item-5"},
    )
    return TemporalGraphSnapshot(catalog, engine, snapshot), engine


def test_known_absent_membership_and_get_do_not_materialize() -> None:
    graph, _ = _graph()
    marker = object()

    assert "item-5" not in graph
    assert graph.get("item-5", marker) is marker
    assert not graph.materialized


def test_defaultdict_getitem_for_known_absent_stays_lazy() -> None:
    graph, _ = _graph()

    created = graph["item-5"]

    assert created == Counter()
    assert graph["item-5"] is created
    assert "item-5" in graph
    assert not graph.materialized


def test_broader_mapping_operations_materialize_exact_graph() -> None:
    graph, engine = _graph()

    assert graph.setdefault("custom", Counter({"neighbor": 2})) == Counter(
        {"neighbor": 2}
    )
    assert graph.materialized
    assert graph["custom"] == Counter({"neighbor": 2})

    marker = object()
    assert graph.pop("missing", marker) is marker
    assert graph.copy().default_factory is Counter
    assert graph.get("item-0", {}) == engine._co.get("item-0", {})
