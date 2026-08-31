from __future__ import annotations

from lingjing_harness.runtime import AgentMemory, RuntimeBackendConfig, build_runtime_tools
from lingjing_harness.runtime.backend_config import OPTIMIZER_BACKEND_ENV
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime import optimizer_tools
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


def _record(sidecar: OptimizerMetaMemory, *, event_key: str, utility: float) -> dict:
    return sidecar.record(
        "catalog-key",
        "search",
        context_key="same-context",
        backend="native",
        context={
            "surface": "search",
            "evidence_route": "proxy",
            "budget_bucket": "small",
            "continuous_dimensions": 2,
            "categorical_bucket": "small",
            "objective_count": 2,
            "constraint_count": 2,
        },
        utility=utility,
        objective_gain=0.03,
        evaluator_calls=8,
        wall_seconds=0.1,
        event_key=event_key,
    )


def _fake_result():
    return {
        "objective_delta": 0.04,
        "candidate_count": 10,
        "candidate_config": {"candidate": "stable"},
        "evaluation_ready": True,
        "evolution": {
            "method": "mixed_genome_response_surface",
            "response_surface": [{}, {}],
        },
    }


def test_meta_sidecar_unwraps_storage_but_preserves_backend_scope():
    base = AgentMemory()
    reference = OptimizerMetaMemory(base)
    semantic_memory = BackendScopedMemory(
        base,
        search_scope="search-semantic-scope",
        recommend_scope="",
        invocation_scope="runtime-semantic-scope",
    )
    semantic = OptimizerMetaMemory(semantic_memory)

    assert semantic.memory is base
    assert semantic.scope_memory is semantic_memory

    reference_write = _record(reference, event_key="same-invocation", utility=0.2)
    semantic_write = _record(semantic, event_key="same-invocation", utility=0.8)

    assert reference_write["recorded"] is True
    assert semantic_write["recorded"] is True
    reference_rows = reference.read("catalog-key", "search")
    semantic_rows = semantic.read("catalog-key", "search")
    assert len(reference_rows) == 1
    assert len(semantic_rows) == 1
    assert reference_rows[0]["mean_utility"] == 0.2
    assert semantic_rows[0]["mean_utility"] == 0.8
    assert reference_rows[0]["catalog_key"] == "catalog-key"
    assert semantic_rows[0]["catalog_key"].startswith("catalog-key:backend:search-semantic-scope")


def test_same_backend_scope_shares_meta_credit_while_different_scope_isolated():
    base = AgentMemory()
    first = OptimizerMetaMemory(
        BackendScopedMemory(base, search_scope="same", invocation_scope="runtime-a")
    )
    second = OptimizerMetaMemory(
        BackendScopedMemory(base, search_scope="same", invocation_scope="runtime-b")
    )
    other = OptimizerMetaMemory(
        BackendScopedMemory(base, search_scope="other", invocation_scope="runtime-c")
    )

    _record(first, event_key="run-1", utility=0.7)

    assert second.read("catalog-key", "search")[0]["trials"] == 1
    assert other.read("catalog-key", "search") == []


def test_runtime_env_accepts_auto_optimizer_policy():
    config = RuntimeBackendConfig.from_env({OPTIMIZER_BACKEND_ENV: "AUTO"})

    assert config.optimizer_backend == "auto"
    assert config.is_dependency_light_default is False


def test_runtime_composes_semantic_backend_with_auto_optimizer_without_credit_collision(
    monkeypatch,
):
    import lingjing_harness.runtime.semantic_tools as semantic_tools

    monkeypatch.setattr(semantic_tools, "FlagEmbeddingSearchAdapter", _FakeSemanticAdapter)
    monkeypatch.setattr(
        optimizer_tools,
        "optimizer_dependency_availability",
        lambda: {
            "native": True,
            "optuna": False,
            "optuna_motpe": False,
            "qlognehvi": False,
        },
    )

    catalog = build_sample_catalog()
    memory = AgentMemory()
    reference = build_runtime_tools(
        catalog,
        memory,
        config=RuntimeBackendConfig(optimizer_backend="auto"),
    )
    semantic = build_runtime_tools(
        catalog,
        memory,
        config=RuntimeBackendConfig(
            search_backend="flag_embedding",
            optimizer_backend="auto",
            search_backend_kwargs={"dense_limit": 2, "model_name": "fake-bge"},
        ),
    )

    first = reference._run_auto(
        "search",
        lambda **kwargs: _fake_result(),
        _invocation_id="same-runtime-invocation",
    )
    second = semantic._run_auto(
        "search",
        lambda **kwargs: _fake_result(),
        _invocation_id="same-runtime-invocation",
    )

    assert reference.optimizer_backend == "auto"
    assert semantic.optimizer_backend == "auto"
    assert first["optimizer_meta_credit"]["recorded"] is True
    assert second["optimizer_meta_credit"]["recorded"] is True
    reference_rows = reference.optimizer_meta_memory.read(reference.catalog_key, "search")
    semantic_rows = semantic.optimizer_meta_memory.read(semantic.catalog_key, "search")
    assert len(reference_rows) == 1
    assert len(semantic_rows) == 1
    assert reference_rows[0]["catalog_key"] != semantic_rows[0]["catalog_key"]
