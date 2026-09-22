from __future__ import annotations

from lingjing_harness.algorithms import SearchConfig, SearchEngine
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.runtime.tools_core import ToolRegistry as CoreToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def _legacy_diagnose(registry: ToolRegistry, query: str) -> dict:
    result = CoreToolRegistry.search_diagnose(registry, query=query)
    segment = registry.segment_router.search_segment(query)
    return {
        **result,
        "segment": segment,
        "strategy_scope": "segment" if segment in registry.search_portfolio else "global",
    }


def test_owned_search_diagnose_reuses_global_preparation(monkeypatch):
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    expected = _legacy_diagnose(registry, query)

    calls = 0
    original = SearchEngine.prepare

    def counted(self, value):
        nonlocal calls
        calls += 1
        return original(self, value)

    monkeypatch.setattr(SearchEngine, "prepare", counted)
    result = registry.search_diagnose(query)

    assert calls == 1
    assert result == expected


def test_search_diagnose_keeps_global_results_when_segment_override_changes_retrieval(monkeypatch):
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    segment = registry.segment_router.search_segment(query)
    registry.search_portfolio[segment] = SearchConfig(candidate_strategy="semantic_rescue")
    expected = _legacy_diagnose(registry, query)

    calls = 0
    original = SearchEngine.prepare

    def counted(self, value):
        nonlocal calls
        calls += 1
        return original(self, value)

    monkeypatch.setattr(SearchEngine, "prepare", counted)
    result = registry.search_diagnose(query)

    assert calls == 1
    assert result == expected
    assert result["strategy_scope"] == "segment"


def test_search_diagnose_preserves_split_backend_route_and_serve_paths():
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    base = registry.search

    class SplitSearchBackend:
        def __init__(self):
            self.config = base.config
            self.routing_calls = 0
            self.serving_calls = 0

        def routing_prepare(self, value):
            self.routing_calls += 1
            return base.prepare(value)

        def search(self, value, *, limit=10):
            self.serving_calls += 1
            return base.search(value, limit=limit)

    backend = SplitSearchBackend()
    registry.search = backend
    registry.segment_router.search = backend

    result = registry.search_diagnose(query)

    assert backend.serving_calls == 1
    assert backend.routing_calls == 1
    assert result["query"] == query
    assert result["result_count"] > 0
