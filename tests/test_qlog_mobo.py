from __future__ import annotations

from types import SimpleNamespace

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms import qlog_mobo
from lingjing_harness.algorithms.optimizer_contracts import (
    OptimizerEvidenceContract,
    OptimizerOutcomeConstraint,
    attach_optimizer_evidence_contract,
)


def _dimensions(*, categorical_choices: tuple[str, ...] = ()):
    rows = [
        core.EvolutionDimension(
            name="weight_a",
            kind="continuous",
            group="blend",
            low=0.1,
            high=0.9,
        ),
        core.EvolutionDimension(
            name="weight_b",
            kind="continuous",
            group="blend",
            low=0.1,
            high=0.9,
        ),
    ]
    if categorical_choices:
        rows.append(
            core.EvolutionDimension(
                name="capability",
                kind="capability",
                group="candidate",
                choices=categorical_choices,
            )
        )
    return rows


def _space(*, categorical_choices: tuple[str, ...] = ()):
    base = {"weight_a": 0.4, "weight_b": 0.6}
    if categorical_choices:
        base["capability"] = categorical_choices[0]
    return qlog_mobo.MixedSearchSpace.build(
        base_config=base,
        dimensions=_dimensions(categorical_choices=categorical_choices),
        group_totals={"blend": 1.0},
    )


def test_mixed_space_round_trip_preserves_categorical_identity_and_blend_mass():
    space = _space(categorical_choices=("lexical", "hybrid", "semantic"))
    config = {"weight_a": 0.7, "weight_b": 0.3, "capability": "semantic"}

    encoded = space.encode(config)
    decoded = space.decode(encoded)

    assert encoded[2] == 2.0
    assert decoded["capability"] == "semantic"
    assert decoded["weight_a"] == pytest.approx(0.7)
    assert decoded["weight_b"] == pytest.approx(0.3)
    assert decoded["weight_a"] + decoded["weight_b"] == pytest.approx(1.0)


def test_normalized_equality_constraint_is_algebraically_exact():
    space = _space()

    assert space.equality_constraint_specs() == [
        ([0, 1], [0.8, 0.8], 0.8)
    ]
    encoded = space.encode({"weight_a": 0.7, "weight_b": 0.3})
    indices, coefficients, rhs = space.equality_constraint_specs()[0]
    lhs = sum(encoded[index] * coefficient for index, coefficient in zip(indices, coefficients))

    assert lhs == pytest.approx(rhs)


def test_small_categorical_space_enumerates_every_assignment_without_ordinality():
    space = _space(categorical_choices=("a", "b", "c"))

    assert space.fixed_features_list() == [
        {2: 0.0},
        {2: 1.0},
        {2: 2.0},
    ]
    provenance = space.provenance()
    assert provenance["categorical_combination_count"] == 3
    assert provenance["categorical_cardinalities"] == {"capability": 3}


def test_reference_point_uses_feasible_initial_rows_when_available():
    reference, basis, rows = qlog_mobo._reference_point(
        [
            (1.0, 10.0, -0.1, 0.0),
            (0.0, 20.0, 0.1, -1.0),
            (2.0, 8.0, -0.2, -0.1),
        ]
    )

    assert basis == "feasible_initial_design"
    assert rows == 2
    assert reference == pytest.approx([0.9, 7.8])


def test_reference_point_falls_back_explicitly_when_initial_design_has_no_feasible_row():
    reference, basis, rows = qlog_mobo._reference_point(
        [
            (1.0, 3.0, 0.1),
            (2.0, 4.0, 0.2),
        ]
    )

    assert basis == "all_initial_design_no_feasible"
    assert rows == 2
    assert reference[0] < 1.0
    assert reference[1] < 3.0


class _FakeTorch:
    double = "double"
    long = "long"

    @staticmethod
    def tensor(value, dtype=None):
        return {"value": value, "dtype": dtype}


def _routing_stack(*, alternating: bool):
    calls: list[tuple[str, dict]] = []

    def record(name):
        def call(**kwargs):
            calls.append((name, kwargs))
            return f"{name}-candidate", f"{name}-value"

        return call

    return SimpleNamespace(
        torch=_FakeTorch(),
        optimize_acqf=record("continuous"),
        optimize_acqf_mixed=record("enumerated"),
        optimize_acqf_mixed_alternating=record("alternating"),
        should_use_mixed_alternating_optimizer=lambda **_: alternating,
        calls=calls,
    )


def test_acquisition_optimizer_uses_continuous_path_without_categorical_genes():
    stack = _routing_stack(alternating=False)
    candidate, value, route = qlog_mobo._optimize_acquisition(
        stack,
        acquisition=object(),
        space=_space(),
    )

    assert route == "continuous_exact"
    assert candidate == "continuous-candidate"
    assert value == "continuous-value"
    assert [name for name, _ in stack.calls] == ["continuous"]


def test_acquisition_optimizer_exactly_enumerates_small_categorical_product():
    stack = _routing_stack(alternating=False)
    candidate, _, route = qlog_mobo._optimize_acquisition(
        stack,
        acquisition=object(),
        space=_space(categorical_choices=("a", "b", "c")),
    )

    assert route == "categorical_enumeration"
    assert candidate == "enumerated-candidate"
    assert [name for name, _ in stack.calls] == ["enumerated"]
    assert stack.calls[0][1]["fixed_features_list"] == [
        {2: 0.0},
        {2: 1.0},
        {2: 2.0},
    ]


def test_acquisition_optimizer_uses_alternating_search_only_for_large_mixed_space():
    stack = _routing_stack(alternating=True)
    candidate, _, route = qlog_mobo._optimize_acquisition(
        stack,
        acquisition=object(),
        space=_space(categorical_choices=("a", "b", "c")),
    )

    assert route == "mixed_alternating"
    assert candidate == "alternating-candidate"
    assert [name for name, _ in stack.calls] == ["alternating"]
    assert stack.calls[0][1]["cat_dims"] == {2: [0.0, 1.0, 2.0]}


def _contract():
    return OptimizerEvidenceContract(
        surface="search",
        evidence_route="proxy",
        objective_names=("primary_objective", "domain_quality"),
        constraints=(
            OptimizerOutcomeConstraint("worse_share", "upper", 0.4),
            OptimizerOutcomeConstraint("worst_delta", "lower", -0.3),
        ),
    )


def _row(x: float, score: float | None = None):
    score = x if score is None else score
    return {
        "config": {"x": x},
        "report": {"quality": 0.5 + 0.1 * x},
        "robustness": {"worse_share": 0.1, "worst_delta": -0.1},
        "objective": score,
        "generation": 0,
        "source": "seed",
    }


def test_outcome_constraint_sign_is_feasible_when_non_positive():
    upper = OptimizerOutcomeConstraint("worse_share", "upper", 0.4)
    lower = OptimizerOutcomeConstraint("worst_delta", "lower", -0.3)

    assert upper.violation({"worse_share": 0.2}) == pytest.approx(-0.2)
    assert upper.violation({"worse_share": 0.6}) == pytest.approx(0.2)
    assert lower.violation({"worst_delta": -0.1}) == pytest.approx(-0.2)
    assert lower.violation({"worst_delta": -0.5}) == pytest.approx(0.2)


def test_qlog_loop_never_spends_budget_on_duplicate_proposals_and_keeps_primary_incumbent(monkeypatch):
    dimensions = [
        core.EvolutionDimension(
            name="x",
            kind="continuous",
            group="independent",
            low=0.0,
            high=1.0,
        )
    ]
    cache_rows = [_row(0.0), _row(0.1), _row(0.2), _row(0.3)]
    cache = {core._config_key(row["config"]): row for row in cache_rows}
    evaluated_x: list[float] = []

    def evaluate(config):
        x = float(config["x"])
        evaluated_x.append(x)
        return (
            {"quality": 0.5 + 0.1 * x},
            {"worse_share": 0.1, "worst_delta": -0.1},
            x,
        )

    attach_optimizer_evidence_contract(evaluate, _contract())
    proposals = iter([0.2, 0.8, 0.9])

    def fake_candidate(*args, **kwargs):
        del args, kwargs
        x = next(proposals)
        return {"x": x}, {
            "reference_point": [0.0, 0.0],
            "acquisition_optimizer": "test",
        }

    monkeypatch.setattr(qlog_mobo, "_candidate_from_acquisition", fake_candidate)
    rows, _ = qlog_mobo.qlognehvi_evolution_loop(
        base_config={"x": 0.0},
        population=[{"x": 0.2}, {"x": 0.8}, {"x": 0.9}],
        dimensions=dimensions,
        group_totals={},
        evaluate=evaluate,
        rng=__import__("random").Random(7),
        cache=cache,
        evaluation_budget=2,
        stack=SimpleNamespace(botorch=SimpleNamespace(__version__="test")),
    )

    assert evaluated_x == [0.8, 0.9]
    assert rows[0]["config"] == {"x": 0.9}
    provenance = rows[0]["optimizer_provenance"]
    assert provenance["native_distinct_evaluation_budget"] == 2
    assert provenance["new_evaluations"] == 2
    assert provenance["duplicate_proposals"] == 1
    assert provenance["final_selection"] == "harness_primary_objective"
    assert provenance["reference_point_frozen_after_initial_design"] is True
