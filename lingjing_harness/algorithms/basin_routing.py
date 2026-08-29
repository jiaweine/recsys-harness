"""Stagnation-aware structural basin routing for vertical evolution.

The core evolver detects a weak local response surface and increases mutation
scale. Scale alone is insufficient when a local optimum is structural: a larger
numeric step can remain in the same mechanism basin. This module adds bounded
combinatorial jump seeds and, when durable evidence exists, second-order mechanism
pair priors learned from the mechanism knowledge graph.

Fresh response-surface measurements remain first-class. Historical pair evidence
can only reorder / suppress exact combinations during stagnation; it never bypasses
typed projection, evaluator budgets, independent holdout, or promotion authority.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any, Iterable

from . import evolution_core as core


MAX_STRUCTURAL_FIELDS = 4
MAX_NUMERIC_FIELDS = 3
MAX_JUMP_CANDIDATES = 8
MAX_PAIR_PRIORS = 6
PAIR_REJECTION_MARGIN = 2
FRESH_PAIR_MIN_ROUTING_SCORE = 0.20

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


def _unique_projected(
    rows: Iterable[dict[str, Any]],
    *,
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
) -> list[dict[str, Any]]:
    """Deduplicate by canonical identity while preserving exact projected mass."""

    seen: set[tuple[tuple[str, Any], ...]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        try:
            projected = core._project(row, dimensions, group_totals)
            key = core._config_key(projected)
        except (TypeError, ValueError, KeyError):
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append(projected)
    return unique


def _pair_key(arms: Iterable[str]) -> tuple[str, str] | None:
    values = sorted({str(value) for value in arms if str(value)})
    if len(values) != 2:
        return None
    return values[0], values[1]


def _mechanism_pair_state(
    remembered: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[tuple[str, str]]]:
    """Return positive interaction priors and repeatedly rejected exact pairs."""

    preferred: list[dict[str, Any]] = []
    blocked: set[tuple[str, str]] = set()
    for row in remembered:
        if not isinstance(row, dict) or row.get("status") != "mechanism_pair":
            continue
        pair = row.get("pair")
        if not isinstance(pair, dict):
            continue
        key = _pair_key(pair.get("arms") or [])
        if key is None:
            continue
        try:
            positive = int(pair.get("positive", 0) or 0)
            negative = int(pair.get("negative", 0) or 0)
            trials = int(pair.get("trials", 0) or 0)
            evidence = int(pair.get("evidence", 0) or 0)
            mean_reward = float(pair.get("mean_reward_delta", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if negative - positive >= PAIR_REJECTION_MARGIN and trials >= PAIR_REJECTION_MARGIN:
            blocked.add(key)
            continue
        if positive > negative:
            preferred.append(
                {
                    "arms": list(key),
                    "positive": positive,
                    "negative": negative,
                    "trials": trials,
                    "evidence": evidence,
                    "mean_reward_delta": mean_reward,
                }
            )
    preferred.sort(
        key=lambda row: (
            -(int(row["positive"]) - int(row["negative"])),
            -int(row["trials"]),
            -int(row["evidence"]),
            -float(row["mean_reward_delta"]),
            tuple(row["arms"]),
        )
    )
    return preferred[:MAX_PAIR_PRIORS], blocked


def _surface_arm_index(surface: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in surface:
        arm = str(row.get("arm") or "")
        if not arm:
            continue
        current = index.get(arm)
        score = (
            float(row.get("routing_score", 0.0) or 0.0),
            float(row.get("objective_delta", 0.0) or 0.0),
        )
        current_score = (
            float(current.get("routing_score", 0.0) or 0.0),
            float(current.get("objective_delta", 0.0) or 0.0),
        ) if current is not None else (-1.0, -1.0)
        if current is None or score > current_score:
            index[arm] = row
    return index


def structural_jump_candidates(
    *,
    base_config: dict[str, Any],
    surface: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float],
    remembered: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build bounded cross-mechanism jump seeds from measured + durable evidence.

    Historical knowledge is deliberately second-order only. A positive pair must
    still have both arms present in the *current* measured response surface and
    both must retain a minimum fresh routing score. Repeated negative pair evidence
    blocks only that exact combination, never either arm individually.
    """

    remembered = remembered or []
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
    preferred_pairs, blocked_pairs = _mechanism_pair_state(remembered)
    arm_index = _surface_arm_index(surface)
    candidates: list[dict[str, Any]] = []

    # Knowledge-graph interaction priors are inserted first, but only when fresh
    # one-arm measurements still show enough local plausibility.
    for prior in preferred_pairs:
        arms = list(prior["arms"])
        rows = [arm_index.get(arm) for arm in arms]
        if any(row is None for row in rows):
            continue
        measured = [row for row in rows if row is not None]
        if len({str(row.get("field") or "") for row in measured}) != 2:
            continue
        if min(float(row.get("routing_score", 0.0) or 0.0) for row in measured) < FRESH_PAIR_MIN_ROUTING_SCORE:
            continue
        row = _combine_measured_arms(
            base_config,
            measured,
            dimensions,
            group_totals,
        )
        if row is not None:
            candidates.append(row)

    for pair in combinations(structural, 2):
        pair_key = _pair_key(str(row.get("arm") or "") for row in pair)
        if pair_key in blocked_pairs:
            continue
        row = _combine_measured_arms(
            base_config,
            pair,
            dimensions,
            group_totals,
        )
        if row is not None:
            candidates.append(row)

    # Structural jumps often expose a new basin whose local optimum needs one
    # numerical retune. Bound this cross product tightly to protect eval budget.
    for structural_row in structural[:3]:
        for numeric_row in numeric[:2]:
            pair_key = _pair_key(
                (
                    str(structural_row.get("arm") or ""),
                    str(numeric_row.get("arm") or ""),
                )
            )
            if pair_key in blocked_pairs:
                continue
            row = _combine_measured_arms(
                base_config,
                (structural_row, numeric_row),
                dimensions,
                group_totals,
            )
            if row is not None:
                candidates.append(row)

    return _unique_projected(
        candidates,
        dimensions=dimensions,
        group_totals=group_totals,
    )[:MAX_JUMP_CANDIDATES]


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
        remembered=remembered,
    )
    if not jumps:
        return population, True

    elite_count = min(3, len(population))
    enhanced = _unique_projected(
        [*population[:elite_count], *jumps, *population[elite_count:]],
        dimensions=dimensions,
        group_totals=group_totals,
    )
    return enhanced[: core.POPULATION_SIZE], True


def _annotated_evolution_metadata(**kwargs: Any) -> dict[str, Any]:
    metadata = dict(_ORIGINAL_EVOLUTION_METADATA(**kwargs))
    metadata.update(
        {
            "basin_router": "measured_structural_jump_with_mechanism_pair_prior",
            "basin_router_version": 3,
            "stagnation_structural_jump": True,
            "mechanism_pair_prior": True,
            "pair_rejection_margin": PAIR_REJECTION_MARGIN,
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
    "MAX_PAIR_PRIORS",
    "PAIR_REJECTION_MARGIN",
    "structural_jump_candidates",
    "install_basin_router",
]
