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
