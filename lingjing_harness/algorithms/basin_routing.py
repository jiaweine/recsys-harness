"""Stagnation-aware structural basin routing for vertical evolution.

The core evolver already detects a weak local response surface and increases
mutation scale.  Scale alone is not enough when the local optimum is structural:
a larger numeric step can remain in the same mechanism basin.  This module adds
bounded combinatorial structural jump seeds when the measured response surface
shows stagnation.

The design borrows the useful separation from bandit-routed self-evolution:
measured local evidence decides *where* to spend budget, while the typed genome
and projector still decide what mutations are legal.  No arbitrary code is
introduced and holdout/trust semantics remain unchanged.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

from . import evolution_core as core


MAX_STRUCTURAL_FIELDS = 4
MAX_NUMERIC_FIELDS = 3
MAX_JUMP_CANDIDATES = 8

_ORIGINAL_SURFACE_SEEDS = core._surface_seeds
_ORIGINAL_EVOLUTION_METADATA = core._evolution_metadata
_INSTALLED = False


def _best_per_field(
    surface: Iterable[dict[str, Any]],
    *,
    kind: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Keep one strongest measured arm per field to avoid redundant jumps."""

    by_field: dict[str, dict[str, Any]] = {}
    for row in surface:
        if str(row.get("kind") or "") != kind:
            continue
        field = str(row.get("field") or "")
        if not field:
            continue
        current = by_field.get(field)
        key = (
            float(row.get("routing_score", 0.0) or 0.0),
            float(row.get("objective_delta", 0.0) or 0.0),
        )
        current_key = (
            float(current.get("routing_score", 0.0) or 0.0),
            float(current.get("objective_delta", 0.0) or 0.0),
        ) if current is not None else (-1.0, -1.0)
        if current is None or key > current_key:
            by_field[field] = row
    return sorted(
        by_field.values(),
        key=lambda row: (
            -float(row.get("routing_score", 0.0) or 0.0),
            -float(row.get("objective_delta", 0.0) or 0.0),
            str(row.get("field") or ""),
        ),
    )[: max(0, int(limit))]


def _combine_measured_arms(
    base_config: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> dict[str, Any] | None:
    candidate = dict(base_config)
    touched: set[str] = set()
    for row in rows:
        field = str(row.get("field") or "")
        config = row.get("config")
        if not field or field in touched or not isinstance(config, dict) or field not in config:
            continue
        candidate[field] = config[field]
        touched.add(field)
    if not touched:
        return None
    try:
        return core._project(candidate, dimensions, group_totals)
    except (TypeError, ValueError, KeyError):
        return None


def structural_jump_candidates(
    *,
    base_config: dict[str, Any],
    surface: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> list[dict[str, Any]]:
    """Build bounded cross-mechanism jump seeds from already measured arms.

    We intentionally use measured arm outputs rather than blind capability
    combinations.  Pairwise structural jumps explore interactions that single-arm
    response surfaces cannot see.  Structural+numeric seeds test whether a new
    mechanism needs a local retune to become competitive.
    """

    structural = _best_per_field(
        surface,
        kind="capability",
        limit=MAX_STRUCTURAL_FIELDS,
    )
    numeric = _best_per_field(
        surface,
        kind="continuous",
        limit=MAX_NUMERIC_FIELDS,
    )
    candidates: list[dict[str, Any]] = []

    for pair in combinations(structural, 2):
        row = _combine_measured_arms(
            base_config,
            pair,
            dimensions,
            group_totals,
        )
        if row is not None:
            candidates.append(row)

    # Structural jumps often expose a new basin whose local optimum needs one
    # numerical retune.  Bound this cross product tightly to protect eval budget.
    for structural_row in structural[:3]:
        for numeric_row in numeric[:2]:
            row = _combine_measured_arms(
                base_config,
                (structural_row, numeric_row),
                dimensions,
                group_totals,
            )
            if row is not None:
                candidates.append(row)

    return core._unique_configs(candidates)[:MAX_JUMP_CANDIDATES]


def _stagnation_aware_surface_seeds(
    *,
    base_config: dict[str, Any],
    surface: list[dict[str, Any]],
    remembered: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    rng: Any,
) -> tuple[list[dict[str, Any]], bool]:
    population, basin_jump = _ORIGINAL_SURFACE_SEEDS(
        base_config=base_config,
        surface=surface,
        remembered=remembered,
        dimensions=dimensions,
        group_totals=group_totals,
        rng=rng,
    )
    if not basin_jump:
        return population, False

    jumps = structural_jump_candidates(
        base_config=base_config,
        surface=surface,
        dimensions=dimensions,
        group_totals=group_totals,
    )
    if not jumps:
        return population, True

    # Keep the very best measured local seeds, then insert cross-mechanism jumps
    # before blind high-scale mutations.  This preserves exploitation while
    # dedicating a bounded fraction of the population to escaping the basin.
    elite_count = min(3, len(population))
    enhanced = core._unique_configs(
        [*population[:elite_count], *jumps, *population[elite_count:]]
    )
    return enhanced[: core.POPULATION_SIZE], True


def _annotated_evolution_metadata(**kwargs: Any) -> dict[str, Any]:
    metadata = dict(_ORIGINAL_EVOLUTION_METADATA(**kwargs))
    metadata.update(
        {
            "basin_router": "measured_structural_combinatorial_jump",
            "basin_router_version": 2,
            "stagnation_structural_jump": True,
            "max_structural_jump_candidates": MAX_JUMP_CANDIDATES,
        }
    )
    return metadata


def install_basin_router() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    core._surface_seeds = _stagnation_aware_surface_seeds
    core._evolution_metadata = _annotated_evolution_metadata
    _INSTALLED = True


__all__ = [
    "MAX_STRUCTURAL_FIELDS",
    "MAX_NUMERIC_FIELDS",
    "MAX_JUMP_CANDIDATES",
    "structural_jump_candidates",
    "install_basin_router",
]
