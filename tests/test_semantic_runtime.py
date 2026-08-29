from __future__ import annotations

import pytest

from lingjing_harness.algorithms import SearchConfig, SearchEngine
from lingjing_harness.domain import Catalog, Item
from lingjing_harness.integrations import FlagEmbeddingHybridSearchEngine
from lingjing_harness.integrations import flag_embedding
from lingjing_harness.runtime import RecommendationBackendToolRegistry, SearchBackendToolRegistry


class _Matrix:
    def __init__(self, rows):
        self.rows = rows

    def __matmul__(self, vector):
        return [sum(left * right for left, right in zip(row, vector)) for row in self.rows]


class _FakeFlagModel:
    init_count = 0
    corpora: list[list[str]] = []

    def __init__(self, model_name, **kwargs):
        type(self).init_count += 1
        self.model_name = model_name
        self.kwargs = kwargs

    def encode_corpus(self, corpus):
        rows = list(corpus)
        type(self).corpora.append(rows)
        assert len(rows) == 2
        return _Matrix([[1.0, 0.0], [0.0, 1.0]])

    def encode_queries(self, queries):
        assert len(queries) == 1
        return [[1.0, 0.0]]


class _FakeCollaborativeAdapter:
    init_count = 0

    def __init__(
        self,
        catalog,
        *,
        model="als",
        min_history=1,
        model_kwargs=None,
        fallback=None,
        **_,
    ):
        type(self).init_count += 1
        self.catalog = catalog
        self.model_name = model
        self.min_history = min_history
        self.model_kwargs = dict(model_kwargs or {})
        self.fallback = fallback

    def capability_manifest(self):
        return {
            "backend": "implicit",
            "model": self.model_name,
            "min_history": self.min_history,
            "training_users": len({row.user_id for row in self.catalog.interactions}),
            "training_items": len(self.catalog.items),
            "training_interactions": len(self.catalog.interactions),
            "fallback": "reference",
        }

    def recommend(self, user_id, *, limit=10):
        return []


def _catalog() -> Catalog:
    return Catalog(
        items=[
            Item("run", "夜间反光腰包", "轻量跑步配件", ["跑步", "安全"]),
            Item("audio", "无线运动耳机", "防汗蓝牙音频", ["耳机", "运动"]),
            Item("hidden", "下架灯具", "不可展示", ["照明"], eligible=False),
        ]
    )


def _replacement_catalog() -> Catalog:
    return Catalog(
        items=[
            Item("camp", "露营营灯", "户外照明装备", ["露营", "照明"]),
            Item("power", "户外电源", "便携储能电池", ["露营", "电源"]),
            Item("hidden-new", "下架帐篷", "不可展示", ["露营"], eligible=False),
        ],
        name="replacement-catalog",
    )


def _reset_fake_flag_model() -> None:
    _FakeFlagModel.init_count = 0
    _FakeFlagModel.corpora = []


def test_reference_backend_does_not_load_optional_semantic_dependency(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("semantic dependency should stay lazy")

    monkeypatch.setattr(flag_embedding, "_load_flag_model", fail_if_loaded)
    registry = SearchBackendToolRegistry(_catalog())

    assert isinstance(registry.search, SearchEngine)
    assert registry.inspect_data()["search_backend"]["backend"] == "reference"


def test_flag_embedding_backend_recovers_zero_anchor_without_erasing_routing_pathology(monkeypatch):
    _reset_fake_flag_model()
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
    _reset_fake_flag_model()
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


def test_semantic_adapter_rebinds_catalog_without_reloading_model(monkeypatch):
    _reset_fake_flag_model()
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    original_catalog = _catalog()
    replacement = _replacement_catalog()
    registry = SearchBackendToolRegistry(
        original_catalog,
        search_backend="flag_embedding",
        search_backend_kwargs={"dense_limit": 2},
    )
    original_adapter = registry.search.adapter
    original_model = original_adapter._model

    registry.replace_catalog(replacement)

    assert _FakeFlagModel.init_count == 1
    assert len(_FakeFlagModel.corpora) == 2
    assert _FakeFlagModel.corpora[-1] == [
        "露营营灯 户外照明装备 露营 照明",
        "户外电源 便携储能电池 露营 电源",
    ]
    assert registry.catalog is replacement
    assert registry.search.adapter is not original_adapter
    assert registry.search.adapter._model is original_model
    assert registry.search.adapter.catalog is replacement
    assert original_adapter.catalog is original_catalog
    assert registry.run_search(query="露营照明")["results"][0]["id"] == "camp"


def test_combined_runtime_reuses_semantic_weights_but_rebuilds_collaborative_state(monkeypatch):
    _reset_fake_flag_model()
    _FakeCollaborativeAdapter.init_count = 0
    monkeypatch.setattr(flag_embedding, "_load_flag_model", lambda: _FakeFlagModel)
    import lingjing_harness.runtime.collaborative_tools as collaborative_tools

    monkeypatch.setattr(
        collaborative_tools,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )
    original = _catalog()
    replacement = _replacement_catalog()
    registry = RecommendationBackendToolRegistry(
        original,
        search_backend="flag_embedding",
        search_backend_kwargs={"dense_limit": 2},
        recommend_backend="implicit_als",
        recommend_backend_kwargs={"min_history": 1},
    )
    semantic_model = registry.search.adapter._model
    collaborative_adapter = registry.recommend.adapter

    registry.replace_catalog(replacement)

    assert _FakeFlagModel.init_count == 1
    assert registry.search.adapter._model is semantic_model
    assert registry.search.adapter.catalog is replacement
    assert _FakeCollaborativeAdapter.init_count == 2
    assert registry.recommend.adapter is not collaborative_adapter
    assert registry.recommend.adapter.catalog is replacement


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
