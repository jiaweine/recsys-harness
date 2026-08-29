from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.algorithms import SearchConfig
from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.basin_routing import structural_jump_candidates


def _measured_surface(base: dict, dimensions, group_totals):
    rows = []
    score = 0.9
    for dimension in dimensions:
        for arm, direction, config in core._neighbors(
            base,
            dimension,
            dimensions,
            group_totals,
        ):
            rows.append(
                {
                    "arm": arm,
                    "field": dimension.name,
                    "kind": dimension.kind,
                    "direction": direction,
                    "routing_score": score,
                    "objective_delta": -0.002,
                    "config": config,
                }
            )
            score -= 0.001
    return rows


def _capability_representatives(surface):
    by_field = {}
    for row in surface:
        if row["kind"] == "capability":
            by_field.setdefault(row["field"], row)
    return list(by_field.values())


def test_structural_jump_combines_distinct_mechanisms() -> None:
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = core._evolution_schema(config)
    surface = _measured_surface(base, dimensions, group_totals)

    candidates = structural_jump_candidates(
        base_config=base,
        surface=surface,
        dimensions=dimensions,
        group_totals=group_totals,
    )

    assert candidates
    signatures = [
        core._config_signature(base, row, dimensions)
        for row in candidates
    ]
    assert any(
        sum(1 for arm in signature if "=" in arm) >= 2
        for signature in signatures
    )


def test_structural_jump_respects_typed_projection() -> None:
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = core._evolution_schema(config)
    surface = _measured_surface(base, dimensions, group_totals)

    for candidate in structural_jump_candidates(
        base_config=base,
        surface=surface,
        dimensions=dimensions,
        group_totals=group_totals,
    ):
        assert core._project(candidate, dimensions, group_totals) == candidate


def test_positive_mechanism_pair_prior_is_seeded_before_generic_pairs() -> None:
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = core._evolution_schema(config)
    surface = _measured_surface(base, dimensions, group_totals)
    capability_rows = _capability_representatives(surface)
    assert len(capability_rows) >= 3
    preferred = capability_rows[-2:]
    preferred_arms = [row["arm"] for row in preferred]

    candidates = structural_jump_candidates(
        base_config=base,
        surface=surface,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=[
            {
                "status": "mechanism_pair",
                "pair": {
                    "arms": preferred_arms,
                    "positive": 3,
                    "negative": 0,
                    "trials": 3,
                    "evidence": 12,
                    "mean_reward_delta": 0.025,
                },
            }
        ],
    )

    first_signature = set(core._config_signature(base, candidates[0], dimensions))
    assert set(preferred_arms).issubset(first_signature)


def test_repeated_negative_pair_blocks_only_the_exact_combination() -> None:
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = core._evolution_schema(config)
    surface = _measured_surface(base, dimensions, group_totals)
    capability_rows = _capability_representatives(surface)
    blocked_arms = [row["arm"] for row in capability_rows[:2]]

    candidates = structural_jump_candidates(
        base_config=base,
        surface=surface,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=[
            {
                "status": "mechanism_pair",
                "pair": {
                    "arms": blocked_arms,
                    "positive": 0,
                    "negative": 3,
                    "trials": 3,
                    "evidence": 10,
                    "mean_reward_delta": -0.04,
                },
            }
        ],
    )

    signatures = [set(core._config_signature(base, row, dimensions)) for row in candidates]
    assert all(not set(blocked_arms).issubset(signature) for signature in signatures)
    # Both arms remain independently usable; only their exact pair is suppressed.
    flattened = set().union(*signatures)
    assert any(arm in flattened for arm in blocked_arms)
