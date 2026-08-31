from types import SimpleNamespace

import pytest

from lingjing_harness.algorithms.optimizer_meta import (
    build_routing_context,
    optimizer_run_utility,
    rank_optimizer_backends,
)


def _continuous(name: str):
    return SimpleNamespace(name=name, kind="continuous", choices=())


def _categorical(name: str, count: int):
    return SimpleNamespace(
        name=name,
        kind="capability",
        choices=tuple(f"choice-{index}" for index in range(count)),
    )


def _context(
    *,
    route: str = "proxy",
    budget: int = 10,
    warm: int = 6,
    dimensions=None,
    objectives: int = 2,
    constraints: int = 2,
):
    dimensions = dimensions or [_continuous("x"), _continuous("y"), _categorical("cap", 3)]
    return build_routing_context(
        surface="search",
        evidence_route=route,
        evaluation_budget=budget,
        dimensions=dimensions,
        cache={index: None for index in range(warm)},
        objective_count=objectives,
        constraint_count=constraints,
    )


def _all_available():
    return {
        "native": True,
        "optuna": True,
        "optuna_motpe": True,
        "qlognehvi": True,
    }


def test_context_key_is_stable_and_structural():
    left = _context()
    right = _context()
    changed = _context(budget=30)

    assert left.context_key == right.context_key
    assert left.context_key != changed.context_key
    assert left.continuous_dimensions == 2
    assert left.categorical_dimensions == 1
    assert left.categorical_cardinality == 3


def test_small_proxy_mixed_space_prefers_tpe_cost_efficiency():
    decision = rank_optimizer_backends(
        _context(route="proxy", budget=10),
        availability=_all_available(),
    )

    assert decision.selected_backend == "optuna"
    assert decision.scores["optuna"] > decision.scores["qlognehvi"]
    assert decision.to_dict()["authority"] == "optimizer_selection_only"


def test_production_constrained_multiobjective_with_warm_start_prefers_qlog():
    decision = rank_optimizer_backends(
        _context(route="production", budget=24, warm=8),
        availability=_all_available(),
    )

    assert decision.selected_backend == "qlognehvi"
    assert decision.scores["qlognehvi"] > decision.scores["optuna"]


def test_large_discrete_space_does_not_force_gp_backend():
    decision = rank_optimizer_backends(
        _context(
            route="proxy",
            budget=20,
            dimensions=[_categorical("a", 8), _categorical("b", 8)],
        ),
        availability=_all_available(),
    )

    assert decision.selected_backend in {"optuna", "optuna_motpe"}
    assert decision.scores[decision.selected_backend] > decision.scores["qlognehvi"]


def test_missing_optional_dependencies_fails_closed_to_native():
    decision = rank_optimizer_backends(
        _context(route="production", budget=24),
        availability={
            "native": True,
            "optuna": False,
            "optuna_motpe": False,
            "qlognehvi": False,
        },
    )

    assert decision.selected_backend == "native"
    assert decision.ranked_backends == ("native",)


def test_exact_context_history_can_overcome_static_prior():
    context = _context(route="proxy", budget=10)
    prior_decision = rank_optimizer_backends(context, availability=_all_available())
    assert prior_decision.selected_backend == "optuna"

    history = [
        {
            "context_key": context.context_key,
            "context": context.to_dict(),
            "backend": "qlognehvi",
            "trials": 40,
            "utility_sum": 39.0,
        }
    ]
    learned = rank_optimizer_backends(
        context,
        history=history,
        availability=_all_available(),
    )

    assert learned.selected_backend == "qlognehvi"
    assert learned.scores["qlognehvi"] > learned.scores["optuna"]


def test_unrelated_history_does_not_hijack_current_context():
    context = _context(route="proxy", budget=10)
    unrelated = _context(
        route="production",
        budget=64,
        dimensions=[_categorical("only", 64)],
    )
    history = [
        {
            "context_key": unrelated.context_key,
            "context": unrelated.to_dict(),
            "backend": "qlognehvi",
            "trials": 80,
            "utility_sum": 80.0,
        }
    ]

    decision = rank_optimizer_backends(
        context,
        history=history,
        availability=_all_available(),
    )

    assert decision.selected_backend == "optuna"


def test_utility_rewards_quality_and_penalizes_proxy_latency_more_strongly():
    proxy = optimizer_run_utility(
        initial_best_objective=1.0,
        final_best_objective=1.08,
        new_evaluations=8,
        wall_seconds=12.0,
        evidence_route="proxy",
    )
    production = optimizer_run_utility(
        initial_best_objective=1.0,
        final_best_objective=1.08,
        new_evaluations=8,
        wall_seconds=12.0,
        evidence_route="production",
    )

    assert proxy["credit_eligible"] is True
    assert production["utility"] > proxy["utility"]
    assert production["latency_scale_seconds"] > proxy["latency_scale_seconds"]


def test_utility_refuses_credit_without_comparable_evaluator_evidence():
    row = optimizer_run_utility(
        initial_best_objective=None,
        final_best_objective=1.0,
        new_evaluations=0,
        wall_seconds=1.0,
        evidence_route="proxy",
    )

    assert row == {
        "credit_eligible": False,
        "utility": None,
        "relative_objective_gain": None,
        "relative_gain_per_evaluator_call": None,
    }
