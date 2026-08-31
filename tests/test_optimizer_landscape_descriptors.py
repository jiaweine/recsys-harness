from lingjing_harness.algorithms.optimizer_meta import (
    build_routing_context,
    describe_optimizer_landscape,
    rank_optimizer_backends,
)
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
