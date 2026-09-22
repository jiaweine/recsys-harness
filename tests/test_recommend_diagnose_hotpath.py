from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace

from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.runtime.tools_core import ToolRegistry


def test_recommend_diagnose_reuses_eligible_snapshot_without_catalog_scan():
    catalog = Catalog(
        items=[
            Item("eligible-a", "A", categories=["cat-a"], eligible=True),
            Item("eligible-b", "B", categories=["cat-b"], eligible=True),
            Item("eligible-c", "C", categories=["cat-c"], eligible=True),
            Item("hidden-d", "D", categories=["hidden"], eligible=False),
        ],
        interactions=[
            Interaction("warm-user", "eligible-a", timestamp=1.0),
            Interaction("warm-user", "eligible-a", timestamp=2.0),
            Interaction("warm-user", "hidden-d", timestamp=3.0),
        ],
    )
    registry = object.__new__(ToolRegistry)
    registry.catalog = catalog
    registry._catalog_inspection = ToolRegistry._build_catalog_inspection(catalog)
    by_user = defaultdict(list)
    for row in catalog.interactions:
        by_user[row.user_id].append(row)
    registry.recommend = SimpleNamespace(_by_user=by_user)

    class NoIteration(list):
        def __iter__(self):
            raise AssertionError("recommend diagnosis must not rescan catalog items")

    catalog.items = NoIteration(catalog.items)

    warm = registry.recommend_diagnose("warm-user")
    cold = registry.recommend_diagnose("cold-user")

    assert warm["history_events"] == 3
    assert warm["seen_items"] == 2
    assert warm["eligible_unseen"] == 2
    assert warm["known_categories"] == ["cat-a", "hidden"]
    assert warm["cold_start"] is False

    assert cold["history_events"] == 0
    assert cold["seen_items"] == 0
    assert cold["eligible_unseen"] == 3
    assert cold["known_categories"] == []
    assert cold["cold_start"] is True
