from __future__ import annotations

import pytest

from lingjing_harness.algorithms import SearchConfig, SearchEngine
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.integrations import FlagEmbeddingHybridSearchEngine
from lingjing_harness.integrations import flag_embedding
from lingjing_harness.runtime import SearchBackendToolRegistry


class _Matrix:
    def __init__(self, rows):
        self.rows = rows

    def __matmul__(self, vector):
        return [sum(left * right for left, right in zip(row, vector)) for row in self.rows]


class _FakeFlagModel:
    init_count = 0

    def __init__(self, model_name, **kwargs):
        type(self).init_count += 1
        self.model_name = model_name
        self.kwargs = kwargs

    def encode_corpus(self, corpus):
        assert len(corpus) == 2
        return _Matrix([[1.0, 0.0], [0.0, 1.0]])

    def encode_queries(self, queries):
        assert len(queries) == 1
        return [[1.0, 0.0]]


def _catalog() -> Catalog:
    return Catalog(
        items=[
            Item("run", "夜间反光腰包", "轻量跑步配件", ["跑步", "安全"]),
            Item("audio", "无线运动耳机", "防汗蓝牙音频", ["耳机", "运动"]),
            Item("hidden", "下架灯具", "不可展示", ["照明"], eligible=False),
        ]
    )


def test_reference_backend_does_not_load_optional_semantic_dependency(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("semantic dependency should stay lazy")

    monkeypatch.setattr(flag_embedding, "_load_flag_model", fail_if_loaded)
    registry = SearchBackendToolRegistry(_catalog())

    assert isinstance(registry.search, SearchEngine)
    assert registry.inspect_data()["search_backend"]["backend"] == "reference"


def test_flag_embedding_backend_recovers_zero_anchor_without_erasing_routing_pathology(monkeypatch):
    _FakeFlagModel.init_count = 0
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    catalog = _catalog()
    query = "户外照明"

    assert SearchEngine(catalog).prepare(query) == []

    registry = SearchBackendToolRegistry(
        catalog,
        search_backend="flag_embedding",
        search_backend_kwargs={
            "dense_limit": 2,
            "model_kwargs": {"devices": "cpu"},
        },
    )
    result = registry.run_search(query=query)

    assert _FakeFlagModel.init_count == 1
    assert isinstance(registry.search, FlagEmbeddingHybridSearchEngine)
    assert result["segment"] == "search/no-anchor"
    assert result["results"][0]["id"] == "run"
    assert result["results"][0]["backend"] == "hybrid_flag_embedding"
    assert registry.inspect_data()["search_backend"]["semantic_owner"] == "flag_embedding"


def test_semantic_backend_reuses_loaded_model_across_configs_and_forks(monkeypatch):
    _FakeFlagModel.init_count = 0
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    registry = SearchBackendToolRegistry(
        _catalog(),
        search_backend="flag_embedding",
        search_backend_kwargs={"dense_limit": 2},
    )

    configured = registry.search.with_config(
        SearchConfig(lexical=0.40, semantic=0.32, title=0.10, quality=0.07, popularity=0.04, freshness=0.07)
    )
    forked = registry.fork()

    assert _FakeFlagModel.init_count == 1
    assert configured.adapter is registry.search.adapter
    assert forked.search.adapter is registry.search.adapter
    assert forked.search_backend == "flag_embedding"


def test_unknown_search_backend_is_rejected_without_loading_optional_dependency(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("semantic dependency should not be loaded")

    monkeypatch.setattr(flag_embedding, "_load_flag_model", fail_if_loaded)

    with pytest.raises(ValueError, match="unknown search backend"):
        SearchBackendToolRegistry(_catalog(), search_backend="unknown")


@pytest.mark.parametrize("dense_limit", [0, -1, 1.5, True])
def test_invalid_dense_limit_is_rejected_before_loading_model(monkeypatch, dense_limit):
    def fail_if_loaded():
        raise AssertionError("invalid backend config must fail before model loading")

    monkeypatch.setattr(flag_embedding, "_load_flag_model", fail_if_loaded)

    with pytest.raises(ValueError):
        SearchBackendToolRegistry(
            _catalog(),
            search_backend="flag_embedding",
            search_backend_kwargs={"dense_limit": dense_limit},
        )
