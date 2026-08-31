from __future__ import annotations

from math import isfinite, sqrt, tanh
from typing import Any, Mapping, Sequence

from .optimizer_meta import (
    OptimizerLandscapeDescriptors,
    _MIN_LANDSCAPE_ROWS,
    _normalized_distance,
    _observation_rows,
    _observation_score,
)


def _finite_weight(value: Any) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not isfinite(weight) or weight <= 0.0:
        return 1.0
    return min(16.0, weight)


def _weighted_mean(values: Sequence[tuple[float, float]]) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0.0:
        return 0.0
    return sum(value * weight for value, weight in values) / total


def _weighted_pstdev(values: Sequence[tuple[float, float]], center: float) -> float:
    total = sum(weight for _, weight in values)
    if total <= 0.0:
        return 0.0
    variance = sum(weight * (value - center) ** 2 for value, weight in values) / total
    return sqrt(max(0.0, variance))


def _weighted_quantile(values: Sequence[tuple[float, float]], quantile: float) -> float:
    ordered = sorted(values, key=lambda item: item[0])
    if not ordered:
        return 0.0
    total = sum(weight for _, weight in ordered)
    if total <= 0.0:
        return ordered[0][0]
    target = min(1.0, max(0.0, float(quantile))) * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def describe_weighted_optimizer_landscape(
    *,
    dimensions: Sequence[Any],
    observations: Sequence[Mapping[str, Any]] | Mapping[Any, Any] | None,
) -> OptimizerLandscapeDescriptors:
    """Summarize durable geometry using routing-only observation weights.

    The caller owns how weights are derived. This function never performs an
    evaluator call and never changes activation or promotion authority.
    """

    raw_rows = _observation_rows(observations)
    scored: list[tuple[Mapping[str, Any], float, float]] = []
    feasibility: list[tuple[bool, float]] = []
    feasibility_complete = True
    for row in raw_rows:
        config = row.get("config")
        score = _observation_score(row)
        if not isinstance(config, Mapping) or score is None:
            continue
        if any(str(getattr(dimension, "name", "") or "") not in config for dimension in dimensions):
            continue
        weight = _finite_weight(row.get("routing_weight"))
        scored.append((config, score, weight))
        feasible = row.get("feasible")
        if isinstance(feasible, bool):
            feasibility.append((feasible, weight))
        else:
            feasibility_complete = False

    if len(scored) < _MIN_LANDSCAPE_ROWS:
        return OptimizerLandscapeDescriptors(rows=len(raw_rows), scored_rows=len(scored))

    weighted_scores = [(score, weight) for _, score, weight in scored]
    score_mean = _weighted_mean(weighted_scores)
    score_low = _weighted_quantile(weighted_scores, 0.05)
    score_high = _weighted_quantile(weighted_scores, 0.95)
    if score_high < score_low:
        score_low, score_high = score_high, score_low
    score_span = max(0.0, score_high - score_low)
    relative_span = score_span / max(0.05, abs(score_mean))

    if score_span <= 1e-12:
        normalized_scores = [0.0 for _ in scored]
    else:
        normalized_scores = [
            min(1.0, max(0.0, (score - score_low) / score_span))
            for _, score, _ in scored
        ]

    local_slopes: list[tuple[float, float]] = []
    for index, (config, _, weight) in enumerate(scored):
        nearest: tuple[float, int] | None = None
        for other_index, (other_config, _, _) in enumerate(scored):
            if other_index == index:
                continue
            distance = _normalized_distance(config, other_config, dimensions)
            if distance is None or distance <= 1e-9:
                continue
            if nearest is None or distance < nearest[0]:
                nearest = (distance, other_index)
        if nearest is None:
            continue
        distance, other_index = nearest
        other_weight = scored[other_index][2]
        pair_weight = sqrt(weight * other_weight)
        slope = abs(normalized_scores[index] - normalized_scores[other_index]) / distance
        local_slopes.append((slope, pair_weight))

    roughness: float | None = None
    slope_dispersion: float | None = None
    if local_slopes:
        slope_mean = _weighted_mean(local_slopes)
        roughness = tanh(slope_mean)
        if len(local_slopes) >= 2:
            slope_dispersion = tanh(
                _weighted_pstdev(local_slopes, slope_mean)
                / max(0.25, slope_mean + 0.25)
            )
        else:
            slope_dispersion = 0.0

    categorical_separations: list[float] = []
    for dimension in dimensions:
        if str(getattr(dimension, "kind", "")) == "continuous":
            continue
        name = str(getattr(dimension, "name", "") or "")
        groups: dict[str, list[tuple[float, float]]] = {}
        for (config, _, weight), normalized in zip(scored, normalized_scores):
            groups.setdefault(str(config.get(name)), []).append((normalized, weight))
        if len(groups) < 2:
            continue
        group_means = [_weighted_mean(values) for values in groups.values() if values]
        if len(group_means) >= 2:
            categorical_separations.append(max(group_means) - min(group_means))

    feasible_density = None
    if feasibility_complete and len(feasibility) == len(scored):
        feasible_weight = sum(weight for feasible, weight in feasibility if feasible)
        total_weight = sum(weight for _, weight in feasibility)
        if total_weight > 0.0:
            feasible_density = feasible_weight / total_weight

    return OptimizerLandscapeDescriptors(
        rows=len(raw_rows),
        scored_rows=len(scored),
        relative_score_span=min(10.0, relative_span),
        local_response_roughness=roughness,
        local_slope_dispersion=slope_dispersion,
        feasible_density=feasible_density,
        categorical_response_separation=(
            max(categorical_separations) if categorical_separations else None
        ),
    )


__all__ = ["describe_weighted_optimizer_landscape"]
