from __future__ import annotations

from lingjing_harness.algorithms import SearchConfig, SearchEngine
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def test_owned_search_run_reuses_routing_preparation(monkeypatch):
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    expected_segment = registry.segment_router.search_segment(query)
    expected_results = registry.search.search(query, limit=8)

    calls = 0
    original = SearchEngine.prepare

    def counted(self, value):
        nonlocal calls
        calls += 1
        return original(self, value)

    monkeypatch.setattr(SearchEngine, "prepare", counted)
    result = registry.run_search(query)

    assert calls == 1
    assert result["segment"] == expected_segment
    assert result["strategy_scope"] == "global"
    assert result["results"] == expected_results


def test_search_run_reprepares_when_segment_retrieval_strategy_changes(monkeypatch):
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    segment = registry.segment_router.search_segment(query)
    config = SearchConfig(candidate_strategy="semantic_rescue")
    registry.search_portfolio[segment] = config
    expected_results = registry.search.with_config(config).search(query, limit=8)

    calls = 0
    original = SearchEngine.prepare

    def counted(self, value):
        nonlocal calls
        calls += 1
        return original(self, value)

    monkeypatch.setattr(SearchEngine, "prepare", counted)
    result = registry.run_search(query)

    assert calls == 2
    assert result["segment"] == segment
    assert result["strategy_scope"] == "segment"
    assert result["results"] == expected_results


def test_search_run_preserves_split_backend_routing_and_serving_paths():
    registry = ToolRegistry(build_sample_catalog(), memory=AgentMemory())
    query = "运动耳机"
    base = registry.search
    expected_results = base.search(query, limit=8)

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

    result = registry.run_search(query)

    assert backend.routing_calls == 1
    assert backend.serving_calls == 1
    assert result["results"] == expected_results
