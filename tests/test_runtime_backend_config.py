from __future__ import annotations

import pytest

from lingjing_harness.integrations import (
    FlagEmbeddingHybridSearchEngine,
    ImplicitHybridRecommendationEngine,
)
from lingjing_harness.runtime import (
    AgentHarness,
    RecommendationBackendToolRegistry,
    RuntimeBackendConfig,
    ToolRegistry,
    build_runtime_tools,
)
from lingjing_harness.runtime.backend_config import (
    OPTIMIZER_BACKEND_ENV,
    RECOMMEND_BACKEND_ENV,
    RECOMMEND_BACKEND_KWARGS_ENV,
    SEARCH_BACKEND_ENV,
    SEARCH_BACKEND_KWARGS_ENV,
)
from lingjing_harness.sample_data import build_sample_catalog


class _FakeSemanticAdapter:
    def __init__(self, catalog, *, model_name="fake-bge", **kwargs):
        self.catalog = catalog
        self.model_name = model_name
        self.kwargs = dict(kwargs)

    def capability_manifest(self):
        return {
            "backend": "flag_embedding",
            "model": self.model_name,
            "corpus_items": len(self.catalog.items),
            "query_instruction": "fake",
        }

    def search(self, query, *, limit=10):
        del query, limit
        return []


class _FakeCollaborativeAdapter:
    def __init__(
        self,
        catalog,
        *,
        model="als",
        min_history=3,
        fallback=None,
        **kwargs,
    ):
        self.catalog = catalog
        self.model_name = model
        self.min_history = int(min_history)
        self.fallback = fallback
        self.kwargs = dict(kwargs)

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

    def history_count(self, user_id):
        return sum(1 for row in self.catalog.interactions if row.user_id == user_id)

    def recommend(self, user_id, *, limit=10):
        del user_id, limit
        return []


def _fake_optional_backends(monkeypatch):
    import lingjing_harness.runtime.collaborative_tools as collaborative_tools
    import lingjing_harness.runtime.semantic_tools as semantic_tools

    monkeypatch.setattr(semantic_tools, "FlagEmbeddingSearchAdapter", _FakeSemanticAdapter)
    monkeypatch.setattr(
        collaborative_tools,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )


def test_dependency_light_defaults_keep_original_registry_and_lazy_optional_imports(monkeypatch):
    import lingjing_harness.integrations.flag_embedding as flag_embedding
    import lingjing_harness.integrations.implicit_recommendation as implicit_recommendation

    monkeypatch.setattr(
        flag_embedding,
        "_load_flag_model",
        lambda: (_ for _ in ()).throw(AssertionError("semantic dependency loaded")),
    )
    monkeypatch.setattr(
        implicit_recommendation,
        "_load_implicit_dependencies",
        lambda: (_ for _ in ()).throw(AssertionError("collaborative dependency loaded")),
    )

    config = RuntimeBackendConfig.from_env({})
    tools = build_runtime_tools(build_sample_catalog(), config=config)

    assert config.is_dependency_light_default is True
    assert type(tools) is ToolRegistry
    assert tools.inspect_data().get("search_backend") is None
    assert tools.inspect_data().get("recommend_backend") is None


def test_environment_contract_parses_backend_names_and_json_kwargs():
    config = RuntimeBackendConfig.from_env(
        {
            SEARCH_BACKEND_ENV: "FLAG_EMBEDDING",
            RECOMMEND_BACKEND_ENV: "IMPLICIT_ALS",
            OPTIMIZER_BACKEND_ENV: "NATIVE",
            SEARCH_BACKEND_KWARGS_ENV: '{"dense_limit": 24, "model_name": "custom-bge"}',
            RECOMMEND_BACKEND_KWARGS_ENV: '{"min_history": 4, "collaborative_limit": 32}',
        }
    )

    assert config.search_backend == "flag_embedding"
    assert config.recommend_backend == "implicit_als"
    assert config.optimizer_backend == "native"
    assert config.search_backend_kwargs == {"dense_limit": 24, "model_name": "custom-bge"}
    assert config.recommend_backend_kwargs == {
        "min_history": 4,
        "collaborative_limit": 32,
    }
    assert config.is_dependency_light_default is False


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({SEARCH_BACKEND_ENV: "mystery"}, "unknown search backend"),
        ({RECOMMEND_BACKEND_ENV: "mystery"}, "unknown recommendation backend"),
        ({OPTIMIZER_BACKEND_ENV: "mystery"}, "unknown optimizer backend"),
        ({SEARCH_BACKEND_KWARGS_ENV: "[]"}, "must be a JSON object"),
        ({RECOMMEND_BACKEND_KWARGS_ENV: "{"}, "must be valid JSON"),
    ],
)
def test_invalid_environment_backend_contract_fails_closed(env, message):
    with pytest.raises(ValueError, match=message):
        RuntimeBackendConfig.from_env(env)


def test_public_harness_composes_explicit_search_and_recommend_backends(monkeypatch):
    _fake_optional_backends(monkeypatch)
    config = RuntimeBackendConfig(
        search_backend="flag_embedding",
        recommend_backend="implicit_als",
        search_backend_kwargs={"dense_limit": 2},
        recommend_backend_kwargs={"min_history": 1, "collaborative_limit": 4},
    )
    harness = AgentHarness(build_sample_catalog(), backend_config=config)

    assert isinstance(harness.tools, RecommendationBackendToolRegistry)
    assert isinstance(harness.tools.search, FlagEmbeddingHybridSearchEngine)
    assert isinstance(harness.tools.recommend, ImplicitHybridRecommendationEngine)
    inspected = harness.tools.inspect_data()
    assert inspected["search_backend"]["backend"] == "flag_embedding"
    assert inspected["recommend_backend"]["backend"] == "implicit"
    assert inspected["optimizer_backend"] == "native"

    forked = harness.fork()
    assert forked.tools.search.adapter is harness.tools.search.adapter
    assert forked.tools.recommend.adapter is harness.tools.recommend.adapter


def test_public_harness_reads_environment_when_registry_is_not_injected(monkeypatch):
    _fake_optional_backends(monkeypatch)
    monkeypatch.setenv(SEARCH_BACKEND_ENV, "flag_embedding")
    monkeypatch.setenv(SEARCH_BACKEND_KWARGS_ENV, '{"dense_limit": 2}')

    harness = AgentHarness(build_sample_catalog())

    assert isinstance(harness.tools, RecommendationBackendToolRegistry)
    assert isinstance(harness.tools.search, FlagEmbeddingHybridSearchEngine)
    assert harness.tools.recommend_backend == "reference"


def test_explicit_tool_injection_is_not_overridden_by_environment(monkeypatch):
    catalog = build_sample_catalog()
    tools = ToolRegistry(catalog)
    monkeypatch.setenv(SEARCH_BACKEND_ENV, "not-a-real-backend")

    harness = AgentHarness(catalog, tools=tools)

    assert harness.tools is tools
