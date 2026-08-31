from dataclasses import asdict
from random import Random

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_meta import (
    build_routing_context,
    describe_optimizer_landscape,
    rank_optimizer_backends,
)
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog
from scripts import optimizer_equal_budget_benchmark as bench


ALL_AVAILABLE = {
    "native": True,
    "optuna": True,
    "optuna_motpe": True,
    "qlognehvi": True,
}


def _benchmark_context(landscape):
    _, cache_configs = bench._initial_design(landscape, 17)
    cache = bench._build_cache(landscape, cache_configs)
    observations = []
    for row in cache.values():
        observation = dict(row)
        observation["feasible"] = bool(bench._is_feasible(row))
        observations.append(observation)
    return build_routing_context(
        surface="search",
        evidence_route="proxy",
        evaluation_budget=10,
        dimensions=landscape.dimensions,
        cache=cache,
        objective_count=2,
        constraint_count=2,
        landscape_observations=observations,
    )


def _durable_search_observations(registry, count=4):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            configs.append(config)
            if len(configs) >= count:
                break
        if len(configs) >= count:
            break
    assert len(configs) >= count
    return [
        {
            "config": config,
            "objective": 0.2 + 0.15 * index,
            "feasible": index % 2 == 0,
            "source": "test_evaluator",
            "generation": index,
            "feasibility_basis": "search_discovery_robustness_guardrails_v1",
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs[:count])
    ]


def test_preobserved_geometry_distinguishes_same_schema_landscapes_without_new_calls():
    smooth, interaction = bench.landscapes()
    smooth_context = _benchmark_context(smooth)
    interaction_context = _benchmark_context(interaction)

    assert smooth_context.continuous_dimensions == interaction_context.continuous_dimensions == 2
    assert smooth_context.categorical_cardinality == interaction_context.categorical_cardinality == 3
    assert smooth_context.evaluation_budget == interaction_context.evaluation_budget == 10
    assert smooth_context.landscape.to_dict()["new_evaluator_calls"] == 0
    assert interaction_context.landscape.to_dict()["new_evaluator_calls"] == 0
    assert smooth_context.context_key != interaction_context.context_key

    assert smooth_context.landscape.score_span_bucket == "compact"
    assert smooth_context.landscape.slope_dispersion_bucket == "stable"
    assert smooth_context.landscape.categorical_response_bucket == "strong"
    assert interaction_context.landscape.score_span_bucket == "extreme"
    assert interaction_context.landscape.slope_dispersion_bucket == "volatile"
    assert interaction_context.landscape.categorical_response_bucket == "weak"


def test_preobserved_geometry_routes_clear_small_surface_to_native_and_interaction_surface_to_tpe():
    smooth, interaction = bench.landscapes()
    smooth_decision = rank_optimizer_backends(
        _benchmark_context(smooth), availability=ALL_AVAILABLE
    )
    interaction_decision = rank_optimizer_backends(
        _benchmark_context(interaction), availability=ALL_AVAILABLE
    )

    assert smooth_decision.selected_backend == "native"
    assert interaction_decision.selected_backend == "optuna"


def test_missing_geometry_preserves_legacy_context_identity():
    landscape = bench.landscapes()[0]
    context = build_routing_context(
        surface="search",
        evidence_route="proxy",
        evaluation_budget=10,
        dimensions=landscape.dimensions,
        cache={index: None for index in range(6)},
        objective_count=2,
        constraint_count=2,
    )

    assert context.landscape.informative is False
    assert context.context_key == "4f997754b94ca1bec146e789"


def test_feasible_density_stays_unknown_when_success_only_memory_has_no_explicit_labels():
    landscape = bench.landscapes()[0]
    observations = [
        {"config": {"x": 0.1, "y": 0.2, "capability": "lexical"}, "score": 0.4},
        {"config": {"x": 0.3, "y": 0.4, "capability": "hybrid"}, "score": 0.5},
        {"config": {"x": 0.5, "y": 0.6, "capability": "semantic"}, "score": 0.6},
        {"config": {"x": 0.7, "y": 0.8, "capability": "hybrid"}, "score": 0.7},
    ]

    descriptors = describe_optimizer_landscape(
        dimensions=landscape.dimensions,
        observations=observations,
    )

    assert descriptors.informative is True
    assert descriptors.feasible_density is None
    assert descriptors.feasible_density_bucket == "unknown"


def test_geometry_mismatch_downweights_contextual_credit_transfer():
    smooth, interaction = bench.landscapes()
    smooth_context = _benchmark_context(smooth)
    interaction_context = _benchmark_context(interaction)
    history = [
        {
            "context_key": smooth_context.context_key,
            "context": smooth_context.to_dict(),
            "backend": "qlognehvi",
            "trials": 80,
            "utility_sum": 79.0,
        }
    ]

    decision = rank_optimizer_backends(
        interaction_context,
        history=history,
        availability=ALL_AVAILABLE,
    )

    assert decision.selected_backend == "optuna"
    assert decision.scores["optuna"] > decision.scores["qlognehvi"]


def test_optimizer_loop_capture_reuses_paid_rows_without_extra_evaluator_calls():
    dimension = core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
        relative_step=0.2,
    )
    calls = []

    def evaluate(config):
        x = float(config["x"])
        calls.append(x)
        bad = x < 0.4
        report = {"queries": 3, "quality": x, "recall": x}
        robust = {
            "worse_share": 0.5 if bad else 0.1,
            "worst_delta": -0.4 if bad else -0.1,
            "mean_delta": 0.0,
        }
        return report, robust, x

    rows, _ = core._evolution_loop(
        base_config={"x": 0.5},
        population=[{"x": 0.2}, {"x": 0.8}],
        dimensions=[dimension],
        group_totals={},
        evaluate=evaluate,
        rng=Random(7),
        cache=None,
    )

    memory = AgentMemory()
    credit = memory.record_evolution_result(
        "catalog",
        "search",
        current_config={"x": 0.5},
        result={
            "evaluation_ready": True,
            "safe_to_try": False,
            "trusted": False,
            "validation": {"holdout": {"independent": False, "samples": 0}},
            "evolution": {"selected_signature": []},
        },
    )
    summary = credit["optimizer_observations"]
    durable = memory.optimizer_observations("catalog", "search")

    assert len(calls) == len(rows)
    assert summary["captured_rows"] == len(rows)
    assert len(durable) == len(rows)
    assert summary["new_evaluator_calls"] == 0
    assert {row["feasible"] for row in durable} == {False, True}


def test_durable_observation_upsert_preserves_mixed_feasibility_without_polluting_evolution_memory():
    memory = AgentMemory()
    observations = [
        {
            "config": {"x": value},
            "objective": score,
            "feasible": feasible,
            "source": "paid_test",
            "generation": index,
            "feasibility_basis": "search_discovery_robustness_guardrails_v1",
        }
        for index, (value, score, feasible) in enumerate(
            [(0.1, 0.2, False), (0.3, 0.4, True), (0.6, 0.7, True), (0.9, 0.1, False)]
        )
    ]

    first = memory.record_optimizer_observations("catalog", "search", observations)
    second = memory.record_optimizer_observations("catalog", "search", observations)
    durable = memory.optimizer_observations("catalog", "search")
    dimension = core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
    )
    descriptors = describe_optimizer_landscape(
        dimensions=[dimension],
        observations=durable,
    )

    assert first["inserted_rows"] == 4
    assert second["inserted_rows"] == 0
    assert second["updated_rows"] == 4
    assert len(durable) == 4
    assert {row["seen_count"] for row in durable} == {2}
    assert descriptors.feasible_density == 0.5
    assert memory.evolution_memory("catalog", "search") == []


def test_durable_observation_geometry_changes_router_context_without_becoming_warm_start():
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    before = registry._routing_context("search")
    observations = _durable_search_observations(registry, 4)

    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        observations,
    )
    after = registry._routing_context("search")
    manifest = registry.inspect_data()["optimizer_meta_router"]

    assert after.landscape.informative is True
    assert after.landscape.feasible_density == 0.5
    assert after.landscape.to_dict()["new_evaluator_calls"] == 0
    assert after.warm_start_rows == before.warm_start_rows
    assert after.context_key != before.context_key
    assert manifest["optimizer_observation_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_memory"] == "evaluator_paid_discovery_rows_only"


def test_sparse_durable_observation_memory_preserves_previous_context_identity():
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    before = registry._routing_context("search")
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _durable_search_observations(registry, 3),
    )
    after = registry._routing_context("search")

    assert after.context_key == before.context_key
    assert after.landscape.feasible_density is None


def test_backend_scoped_optimizer_observations_do_not_cross_serving_namespaces():
    base = AgentMemory()
    scoped = BackendScopedMemory(base, search_scope="semantic-a")
    observations = [
        {
            "config": {"x": 0.2},
            "objective": 0.3,
            "feasible": False,
            "source": "paid_test",
            "generation": 0,
            "feasibility_basis": "search_discovery_robustness_guardrails_v1",
        }
    ]

    scoped.record_optimizer_observations("catalog", "search", observations)
    scoped_rows = scoped.optimizer_observations("catalog", "search")
    unscoped_rows = base.optimizer_observations("catalog", "search")

    assert len(scoped_rows) == 1
    assert unscoped_rows == []
