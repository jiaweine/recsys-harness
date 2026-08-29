from __future__ import annotations

from dataclasses import asdict, replace
import time

import pytest

from lingjing_harness.algorithms import RecommendConfig, SearchConfig
from lingjing_harness.integrations import (
    FlagEmbeddingHybridSearchEngine,
    ImplicitHybridRecommendationEngine,
)
from lingjing_harness.runtime import (
    AgentHarness,
    AgentMemory,
    RecommendationBackendToolRegistry,
    RuntimeBackendConfig,
    ToolRegistry,
    build_runtime_tools,
    catalog_fingerprint,
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


def _active(memory, catalog_key, domain, config):
    return memory.remember_strategy(
        catalog_key,
        domain,
        asdict(config),
        score=0.8,
        evidence=8,
        status="active",
        payload={"validated_at": time.time()},
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
    assert config.strategy_scopes == {"search": "", "recommend": ""}
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
    assert config.strategy_scopes["search"].startswith("search-")
    assert config.strategy_scopes["recommend"].startswith("recommend-")
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


def test_non_json_backend_options_are_rejected_before_identity_is_created():
    with pytest.raises(ValueError, match="JSON-compatible"):
        RuntimeBackendConfig(
            search_backend="flag_embedding",
            search_backend_kwargs={"model_kwargs": {"device": object()}},
        )


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
    assert harness.tools.runtime_backend_config["strategy_scopes"] == config.strategy_scopes

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


def test_strategy_memory_is_scoped_by_serving_backend_but_not_other_surface(monkeypatch):
    _fake_optional_backends(monkeypatch)
    catalog = build_sample_catalog()
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)

    reference_search = replace(SearchConfig(), diversity=0.12)
    reference_recommend = replace(RecommendConfig(), diversity=0.14)
    _active(memory, key, "search", reference_search)
    _active(memory, key, "recommend", reference_recommend)

    semantic_config = RuntimeBackendConfig(
        search_backend="flag_embedding",
        search_backend_kwargs={"dense_limit": 2, "model_name": "fake-bge"},
    )
    semantic = build_runtime_tools(catalog, memory, config=semantic_config)

    assert semantic.catalog_key == key
    assert semantic.search.config == SearchConfig()
    assert semantic.recommend.config == reference_recommend

    semantic_search = replace(SearchConfig(), diversity=0.21)
    _active(semantic.memory, key, "search", semantic_search)

    same_search_new_recommend = build_runtime_tools(
        catalog,
        memory,
        config=RuntimeBackendConfig(
            search_backend="flag_embedding",
            recommend_backend="implicit_als",
            search_backend_kwargs={"dense_limit": 2, "model_name": "fake-bge"},
            recommend_backend_kwargs={"min_history": 1, "collaborative_limit": 4},
        ),
    )
    assert same_search_new_recommend.search.config == semantic_search
    assert same_search_new_recommend.recommend.config == RecommendConfig()

    reference = build_runtime_tools(catalog, memory, config=RuntimeBackendConfig())
    assert reference.search.config == reference_search
    assert reference.recommend.config == reference_recommend


def test_backend_kwargs_change_strategy_identity_and_invocation_replay(monkeypatch):
    _fake_optional_backends(monkeypatch)
    catalog = build_sample_catalog()
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)
    first = build_runtime_tools(
        catalog,
        memory,
        config=RuntimeBackendConfig(
            search_backend="flag_embedding",
            search_backend_kwargs={"dense_limit": 2, "model_name": "first"},
        ),
    )
    second = build_runtime_tools(
        catalog,
        memory,
        config=RuntimeBackendConfig(
            search_backend="flag_embedding",
            search_backend_kwargs={"dense_limit": 3, "model_name": "second"},
        ),
    )
    first_config = replace(SearchConfig(), diversity=0.18)
    second_config = replace(SearchConfig(), diversity=0.24)

    first.memory.remember_strategy(
        key,
        "search",
        asdict(first_config),
        score=0.7,
        evidence=5,
        status="active",
        payload={"validated_at": time.time()},
        invocation_id="same-invocation",
        tool_result={"backend": "first"},
    )
    second.memory.remember_strategy(
        key,
        "search",
        asdict(second_config),
        score=0.8,
        evidence=5,
        status="active",
        payload={"validated_at": time.time()},
        invocation_id="same-invocation",
        tool_result={"backend": "second"},
    )

    assert first.memory.active_config(key, "search") == asdict(first_config)
    assert second.memory.active_config(key, "search") == asdict(second_config)
    assert first.memory.invocation_result("same-invocation")["result"]["backend"] == "first"
    assert second.memory.invocation_result("same-invocation")["result"]["backend"] == "second"
    assert memory.invocation_result("same-invocation") is None


def test_explicit_tool_injection_is_not_overridden_by_environment(monkeypatch):
    catalog = build_sample_catalog()
    tools = ToolRegistry(catalog)
    monkeypatch.setenv(SEARCH_BACKEND_ENV, "not-a-real-backend")

    harness = AgentHarness(catalog, tools=tools)

    assert harness.tools is tools
