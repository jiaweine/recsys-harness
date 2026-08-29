from types import SimpleNamespace

import pytest

from lingjing_harness.algorithms import SearchEngine, evolve_search
from lingjing_harness.algorithms import optimizer_backends
from lingjing_harness.runtime import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def test_native_optimizer_backend_remains_default():
    catalog = build_sample_catalog()
    result = evolve_search(catalog, SearchEngine(catalog))

    assert optimizer_backends.current_optimizer_backend() == "native"
    assert result["evolution"]["method"] == "mixed_genome_response_surface"


def test_explicit_optimizer_context_is_restored(monkeypatch):
    monkeypatch.setattr(optimizer_backends, "_load_optuna", lambda: object())

    assert optimizer_backends.current_optimizer_backend() == "native"
    with optimizer_backends.optimizer_backend("optuna"):
        assert optimizer_backends.current_optimizer_backend() == "optuna"
    assert optimizer_backends.current_optimizer_backend() == "native"


def test_optuna_dependency_failure_happens_before_search_evaluation(monkeypatch):
    catalog = build_sample_catalog()
    engine = SearchEngine(catalog)
    calls = 0
    original_search = engine.search

    def counted_search(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_search(*args, **kwargs)

    monkeypatch.setattr(engine, "search", counted_search)

    def missing():
        raise RuntimeError("optimizer extra is required")

    monkeypatch.setattr(optimizer_backends, "_load_optuna", missing)
    with pytest.raises(RuntimeError, match="optimizer extra is required"):
        evolve_search(catalog, engine, optimizer_backend="optuna")
    assert calls == 0


def test_optimizer_tool_registry_preserves_backend_across_fork():
    catalog = build_sample_catalog()
    registry = OptimizerToolRegistry(catalog, optimizer_backend="native")
    child = registry.fork()

    assert registry.optimizer_backend == "native"
    assert child.optimizer_backend == "native"
    assert registry.inspect_data()["optimizer_backends"] == ["native", "optuna"]


def test_unknown_optimizer_backend_is_rejected():
    with pytest.raises(ValueError, match="unknown optimizer backend"):
        with optimizer_backends.optimizer_backend("mystery"):
            pass


def test_native_distinct_evaluation_budget_matches_population_contract():
    population = [{"gene": index} for index in range(10)]
    cache = {
        optimizer_backends.core._config_key(config): {"config": config}
        for config in population[:6]
    }

    assert optimizer_backends._native_distinct_evaluation_budget(population, cache) == 9


def test_optuna_annotation_exposes_distinct_evaluation_provenance(monkeypatch):
    monkeypatch.setattr(
        optimizer_backends,
        "_load_optuna",
        lambda: SimpleNamespace(__version__="4.9.0"),
    )
    result = {
        "candidate_count": 7,
        "generations": 2,
        "evolution": {
            "method": "mixed_genome_response_surface",
            "response_surface": [{}, {}, {}],
        },
    }

    annotated = optimizer_backends.annotate_optimizer_backend(result, "optuna")
    evolution = annotated["evolution"]

    assert evolution["optimizer_budget_contract"] == "native_distinct_evaluator_calls"
    assert evolution["optimizer_new_evaluations"] == 4
    assert annotated["generations"] == 0
