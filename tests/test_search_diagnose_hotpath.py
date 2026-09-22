from __future__ import annotations

from types import SimpleNamespace

from lingjing_harness.domain import Catalog, Item
from lingjing_harness.runtime.tools_core import ToolRegistry


def test_search_diagnose_uses_item_index_and_preserves_missing_result_fallback():
    catalog = Catalog(
        items=[
            Item("known", "alpha result"),
            Item("other", "unrelated"),
        ]
    )
    registry = object.__new__(ToolRegistry)
    registry.catalog = catalog
    registry.search = SimpleNamespace(
        search=lambda query, limit: [
            {
                "id": "known",
                "title": "wrong fallback",
                "signals": {"match": 0.8},
            },
            {
                "id": "missing",
                "title": "beta fallback",
                "signals": {"match": 0.7},
            },
        ]
    )

    class NoIteration(list):
        def __iter__(self):
            raise AssertionError("search diagnosis must not scan catalog items")

    catalog.items = NoIteration(catalog.items)
    result = registry.search_diagnose("alpha beta")

    assert result["result_count"] == 2
    assert result["covered_tokens"] == ["alpha", "beta"]
    assert result["top_match"] == 0.8
    assert result["diagnosis"] == "当前查询的直接词项证据基本完整"
