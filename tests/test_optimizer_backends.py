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
    monkeypatch.setattr(optimizer_backends, "_load_qlog", lambda: object())

    assert optimizer_backends.current_optimizer_backend() == "native"
    with optimizer_backends.optimizer_backend("optuna"):
        assert optimizer_backends.current_optimizer_backend() == "optuna"
    with optimizer_backends.optimizer_backend("optuna_motpe"):
        assert optimizer_backends.current_optimizer_backend() == "optuna_motpe"
    with optimizer_backends.optimizer_backend("qlognehvi"):
        assert optimizer_backends.current_optimizer_backend() == "qlognehvi"
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
        evolve_search(catalog, engine, optimizer_backend="optuna_motpe")
    assert calls == 0


def test_qlog_dependency_failure_happens_before_search_evaluation(monkeypatch):
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
        raise RuntimeError("mobo extra is required")

    monkeypatch.setattr(optimizer_backends, "_load_qlog", missing)
    with pytest.raises(RuntimeError, match="mobo extra is required"):
        evolve_search(catalog, engine, optimizer_backend="qlognehvi")
    assert calls == 0


def test_optimizer_tool_registry_preserves_backend_across_fork():
    catalog = build_sample_catalog()
    registry = OptimizerToolRegistry(catalog, optimizer_backend="native")
    child = registry.fork()

    assert registry.optimizer_backend == "native"
    assert child.optimizer_backend == "native"
    assert registry.inspect_data()["optimizer_backends"] == [
        "native",
        "optuna",
        "optuna_motpe",
        "qlognehvi",
    ]


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


def test_motpe_values_keep_quality_and_robustness_separate():
    values = optimizer_backends._motpe_values(
        {
            "objective": 0.42,
            "report": {"quality": 0.81, "business_reward": 0.95},
            "robustness": {"worse_share": 0.125},
        }
    )

    assert values == (0.42, 0.81, -0.125)


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
    assert evolution["pareto_search"] is False
    assert annotated["generations"] == 0


def test_motpe_annotation_exposes_pareto_provenance(monkeypatch):
    monkeypatch.setattr(
        optimizer_backends,
        "_load_optuna",
        lambda: SimpleNamespace(__version__="4.9.0"),
    )
    result = {
        "candidate_count": 9,
        "generations": 2,
        "evolution": {
            "method": "mixed_genome_response_surface",
            "response_surface": [{}, {}, {}, {}],
        },
    }

    annotated = optimizer_backends.annotate_optimizer_backend(result, "optuna_motpe")
    evolution = annotated["evolution"]

    assert evolution["method"] == "optuna_motpe_with_evidence_response_surface"
    assert evolution["pareto_search"] is True
    assert evolution["optimizer_objectives"] == [
        "primary_objective",
        "domain_quality",
        "negative_worse_share",
    ]
    assert evolution["final_selection"] == "harness_primary_objective"
    assert evolution["optimizer_new_evaluations"] == 5


def test_qlog_annotation_exposes_constraint_and_budget_provenance(monkeypatch):
    telemetry = {
        "contract": {
            "objectives": ["primary_objective", "domain_quality"],
            "constraints": [
                {
                    "metric": "worse_share",
                    "relation": "upper",
                    "threshold": 0.4,
                    "feasible_when": "modeled_value<=0",
                }
            ],
            "evidence_route": "proxy",
        },
        "model": "ModelListGP[independent_single_output_gp]",
        "acquisition": "qLogNoisyExpectedHypervolumeImprovement",
        "new_evaluations": 3,
        "reference_point_basis": "feasible_initial_design",
    }
    monkeypatch.setattr(
        optimizer_backends,
        "_load_qlog",
        lambda: SimpleNamespace(botorch=SimpleNamespace(__version__="0.18.1")),
    )
    optimizer_backends._QLOG_TELEMETRY.set(telemetry)
    result = {
        "candidate_count": 10,
        "generations": 2,
        "evolution": {
            "method": "mixed_genome_response_surface",
            "response_surface": [{}, {}, {}, {}],
        },
    }

    annotated = optimizer_backends.annotate_optimizer_backend(result, "qlognehvi")
    evolution = annotated["evolution"]

    assert evolution["optimizer_backend"] == "qlognehvi"
    assert evolution["optimizer_library"] == "botorch"
    assert evolution["optimizer_version"] == "0.18.1"
    assert evolution["optimizer_budget_contract"] == "native_distinct_evaluator_calls"
    assert evolution["optimizer_new_evaluations"] == 3
    assert evolution["pareto_search"] is True
    assert evolution["noisy_multiobjective"] is True
    assert evolution["optimizer_evidence_route"] == "proxy"
    assert evolution["final_selection"] == "harness_primary_objective"
    assert evolution["optimizer_outcome_constraints"][0]["metric"] == "worse_share"
