from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass
from hashlib import blake2b
from math import isfinite
from random import Random
from statistics import mean
from typing import Any, Callable, Iterable, TypeVar

from lingjing_harness.domain import Catalog
from .capabilities import CAPABILITIES, normalize_strategy_config
from .evaluation import audit_cold_start, ndcg_at_k, recall_at_k, reciprocal_rank
from .recommend import RecommendConfig, RecommendationEngine
from .search import SearchConfig, SearchEngine


MIN_SEARCH_EVIDENCE = 3
MIN_RECOMMEND_EVIDENCE = 3
MAX_GENERATIONS = 2
POPULATION_SIZE = 10
MAX_EVOLUTION_DIMENSIONS = 24
MAX_EVOLUTION_SAMPLES = 36
T = TypeVar("T")


@dataclass(frozen=True)
class EvolutionDimension:
    """One typed gene discovered from a vertical strategy schema."""

    name: str
    kind: str
    group: str
    low: float = 0.0
    high: float = 0.0
    relative_step: float = 0.0
    choices: tuple[str, ...] = ()

    def dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "group": self.group,
        }
        if self.kind == "continuous":
            row.update(
                {
                    "min": self.low,
                    "max": self.high,
                    "relative_step": self.relative_step,
                }
            )
        else:
            row["choices"] = list(self.choices)
        return row


def _seed(catalog: Catalog, domain: str) -> int:
    raw = (
        f"{domain}|{catalog.name}|{len(catalog.items)}|"
        f"{len(catalog.interactions)}|{len(catalog.query_labels)}"
    )
    for item in catalog.items[:32]:
        raw += f"|{item.item_id}:{item.title}"
    return int.from_bytes(blake2b(raw.encode("utf-8"), digest_size=8).digest(), "little")


def _stable_split(rows: list[T], key: Callable[[T], str]) -> tuple[list[T], list[T]]:
    """Split by unique evaluation identity so one identity cannot leak across sets."""

    unique: dict[str, T] = {}
    for row in rows:
        unique.setdefault(str(key(row)), row)
    ranked = sorted(
        unique.values(),
        key=lambda row: blake2b(str(key(row)).encode("utf-8"), digest_size=8).digest(),
    )
    if len(ranked) < 4:
        return ranked, []
    holdout_size = max(1, min(len(ranked) // 3, len(ranked) - 2))
    return ranked[holdout_size:], ranked[:holdout_size]


def _stable_limit(rows: list[T], key: Callable[[T], str], limit: int = MAX_EVOLUTION_SAMPLES) -> list[T]:
    if len(rows) <= limit:
        return list(rows)
    return sorted(
        rows,
        key=lambda row: blake2b(str(key(row)).encode("utf-8"), digest_size=8).digest(),
    )[:limit]


def _evolution_schema(config: Any) -> tuple[list[EvolutionDimension], dict[str, float]]:
    """Discover continuous and structural genes from dataclass metadata."""

    if not is_dataclass(config):
        raise TypeError("evolution config must be a dataclass instance")
    config = normalize_strategy_config(config)
    dimensions: list[EvolutionDimension] = []
    group_totals: dict[str, float] = {}
    for config_field in fields(config):
        metadata = config_field.metadata
        capability_group = str(metadata.get("capability_group") or "")
        evolve_group = str(metadata.get("evolve_group") or "")
        if capability_group:
            choices = CAPABILITIES.names(capability_group)
            if not choices:
                raise ValueError(f"capability group has no registered choices: {capability_group}")
            dimensions.append(
                EvolutionDimension(
                    name=config_field.name,
                    kind="capability",
                    group=capability_group,
                    choices=choices,
                )
            )
            continue
        if evolve_group:
            value = float(getattr(config, config_field.name))
            low = float(metadata.get("min", 0.0))
            high = float(metadata.get("max", max(1.0, value * 3.0)))
            relative_step = max(0.02, float(metadata.get("relative_step", 0.15)))
            dimensions.append(
                EvolutionDimension(
                    name=config_field.name,
                    kind="continuous",
                    group=evolve_group,
                    low=low,
                    high=high,
                    relative_step=relative_step,
                )
            )
            if evolve_group != "independent":
                group_totals[evolve_group] = group_totals.get(evolve_group, 0.0) + value
    if not dimensions:
        raise ValueError("config exposes no evolvable dimensions")
    return dimensions[:MAX_EVOLUTION_DIMENSIONS], group_totals


def _canonical_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        number = float(value)
        if not isfinite(number):
            raise ValueError("non-finite strategy value")
        return round(number, 7)
    return str(value)


def _config_key(config: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted((str(key), _canonical_value(value)) for key, value in config.items()))


def _unique_configs(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[tuple[str, Any], ...]] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            key = _config_key(row)
        except (TypeError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({name: _canonical_value(value) for name, value in row.items()})
    return out


def _clip(value: float, dimension: EvolutionDimension) -> float:
    if not isfinite(value):
        raise ValueError(f"non-finite gene: {dimension.name}")
    return max(dimension.low, min(dimension.high, float(value)))


def _rebalance_group(
    row: dict[str, Any],
    members: list[EvolutionDimension],
    target: float,
) -> None:
    """Project one bounded blend group onto its exact target mass.

    The old one-shot correction could leave a group off-mass after bounds clipped
    the correction itself. Capacity-weighted redistribution converges while
    respecting every field bound.
    """

    low_total = sum(dimension.low for dimension in members)
    high_total = sum(dimension.high for dimension in members)
    if target < low_total - 1e-9 or target > high_total + 1e-9:
        raise ValueError("blend target is outside declared bounds")

    for _ in range(max(4, len(members) * 2)):
        total = sum(float(row[dimension.name]) for dimension in members)
        delta = target - total
        if abs(delta) <= 1e-9:
            return
        if delta > 0:
            capacities = {
                dimension.name: max(0.0, dimension.high - float(row[dimension.name]))
                for dimension in members
            }
        else:
            capacities = {
                dimension.name: max(0.0, float(row[dimension.name]) - dimension.low)
                for dimension in members
            }
        capacity = sum(capacities.values())
        if capacity <= 1e-12:
            break
        magnitude = min(abs(delta), capacity)
        sign = 1.0 if delta > 0 else -1.0
        for dimension in members:
            share = capacities[dimension.name] / capacity
            row[dimension.name] = _clip(
                float(row[dimension.name]) + sign * magnitude * share,
                dimension,
            )

    total = sum(float(row[dimension.name]) for dimension in members)
    if abs(total - target) > 1e-7:
        raise ValueError("unable to project blend group onto target mass")


def _project(
    values: dict[str, Any],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
) -> dict[str, Any]:
    row = dict(values)
    by_group: dict[str, list[EvolutionDimension]] = {}
    for dimension in dimensions:
        if dimension.kind == "capability":
            current = str(row.get(dimension.name, ""))
            if current not in dimension.choices:
                current = CAPABILITIES.default(dimension.group)
            row[dimension.name] = current
            continue
        if dimension.name not in row:
            raise KeyError(dimension.name)
        row[dimension.name] = _clip(float(row[dimension.name]), dimension)
        if dimension.group != "independent":
            by_group.setdefault(dimension.group, []).append(dimension)

    for group, members in by_group.items():
        target = float(group_totals[group])
        _rebalance_group(row, members, target)
    return row


def _dimension_step(base: dict[str, Any], dimension: EvolutionDimension, scale: float = 1.0) -> float:
    if dimension.kind != "continuous":
        raise TypeError("step size only applies to continuous genes")
    value = abs(float(base[dimension.name]))
    floor = max(0.008, (dimension.high - dimension.low) * 0.018)
    return max(floor, value * dimension.relative_step) * max(0.25, float(scale))


def _perturb(
    base: dict[str, Any],
    dimension: EvolutionDimension,
    direction: int,
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
    *,
    scale: float = 1.0,
) -> dict[str, Any]:
    if dimension.kind != "continuous":
        raise TypeError("_perturb expects a continuous dimension")
    row = dict(base)
    row[dimension.name] = float(row[dimension.name]) + (
        1 if direction >= 0 else -1
    ) * _dimension_step(base, dimension, scale)
    return _project(row, dimensions, group_totals)


def _neighbors(
    base: dict[str, Any],
    dimension: EvolutionDimension,
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
) -> list[tuple[str, str, dict[str, Any]]]:
    if dimension.kind == "continuous":
        return [
            (f"{dimension.name}:up", "up", _perturb(base, dimension, 1, dimensions, group_totals)),
            (f"{dimension.name}:down", "down", _perturb(base, dimension, -1, dimensions, group_totals)),
        ]
    current = str(base.get(dimension.name) or CAPABILITIES.default(dimension.group))
    rows = []
    for choice in dimension.choices:
        if choice == current:
            continue
        candidate = dict(base)
        candidate[dimension.name] = choice
        rows.append(
            (
                f"{dimension.name}={choice}",
                choice,
                _project(candidate, dimensions, group_totals),
            )
        )
    return rows


def _arm_names(
    base: dict[str, Any],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
) -> list[str]:
    return [
        arm
        for dimension in dimensions
        for arm, _, _ in _neighbors(base, dimension, dimensions, group_totals)
    ]


def _trusted_memory_rows(remembered: list[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for memory in remembered:
        if not isinstance(memory, dict):
            continue
        status = str(memory.get("status") or "")
        if status and status not in {"trusted", "active"}:
            continue
        config = memory.get("config")
        if isinstance(config, dict):
            yield memory


def _history_posteriors(
    base: dict[str, Any],
    remembered: list[dict[str, Any]],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    """Convert validated strategies into robust dynamic Beta priors."""

    group_totals = group_totals or {}
    posterior = {arm: [1.0, 1.0] for arm in _arm_names(base, dimensions, group_totals)}
    for memory in _trusted_memory_rows(remembered):
        config = memory["config"]
        try:
            wins = max(1.0, min(6.0, float(memory.get("wins", 1) or 1)))
        except (TypeError, ValueError):
            wins = 1.0
        for dimension in dimensions:
            if dimension.name not in config:
                continue
            if dimension.kind == "capability":
                choice = str(config[dimension.name])
                target = f"{dimension.name}={choice}"
                if target in posterior and choice != str(base.get(dimension.name)):
                    posterior[target][0] += wins
                    for arm in posterior:
                        if arm.startswith(f"{dimension.name}=") and arm != target:
                            posterior[arm][1] += 0.5 * wins
                continue
            try:
                before = float(base[dimension.name])
                after = float(config[dimension.name])
            except (TypeError, ValueError):
                continue
            if not isfinite(after):
                continue
            epsilon = max(1e-5, abs(before) * 0.025)
            up = f"{dimension.name}:up"
            down = f"{dimension.name}:down"
            if after > before + epsilon:
                if up in posterior:
                    posterior[up][0] += wins
                if down in posterior:
                    posterior[down][1] += wins
            elif after < before - epsilon:
                if down in posterior:
                    posterior[down][0] += wins
                if up in posterior:
                    posterior[up][1] += wins
    return {key: (value[0], value[1]) for key, value in posterior.items()}


def _config_signature(
    base: dict[str, Any],
    row: dict[str, Any],
    dimensions: list[EvolutionDimension],
) -> tuple[str, ...]:
    signature: list[str] = []
    for dimension in dimensions:
        if dimension.kind == "capability":
            before = str(base.get(dimension.name, ""))
            after = str(row.get(dimension.name, before))
            if after != before:
                signature.append(f"{dimension.name}={after}")
            continue
        before = float(base[dimension.name])
        after = float(row[dimension.name])
        epsilon = max(1e-5, abs(before) * 0.025)
        if after > before + epsilon:
            signature.append(f"{dimension.name}:up")
        elif after < before - epsilon:
            signature.append(f"{dimension.name}:down")
    return tuple(signature) or ("local:neutral",)


def _mutate_config(
    base: dict[str, Any],
    *,
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
    rng: Random,
    scale: float,
) -> dict[str, Any]:
    row = dict(base)
    touched = 0
    for dimension in dimensions:
        if dimension.kind == "capability":
            probability = min(0.48, 0.18 + 0.10 * max(0.0, scale - 0.7))
            if rng.random() > probability:
                continue
            alternatives = [choice for choice in dimension.choices if choice != str(row.get(dimension.name))]
            if alternatives:
                row[dimension.name] = alternatives[rng.randrange(len(alternatives))]
                touched += 1
            continue
        if rng.random() > 0.58:
            continue
        direction = 1 if rng.random() >= 0.5 else -1
        amplitude = rng.uniform(0.45, 1.15) * scale
        row[dimension.name] = float(row[dimension.name]) + direction * _dimension_step(
            row, dimension, amplitude
        )
        touched += 1

    if not touched:
        dimension = dimensions[rng.randrange(len(dimensions))]
        if dimension.kind == "capability":
            alternatives = [choice for choice in dimension.choices if choice != str(row.get(dimension.name))]
            if alternatives:
                row[dimension.name] = alternatives[rng.randrange(len(alternatives))]
        else:
            direction = 1 if rng.random() >= 0.5 else -1
            row[dimension.name] = float(row[dimension.name]) + direction * _dimension_step(
                row, dimension, scale
            )
    return _project(row, dimensions, group_totals)


def _response_surface(
    *,
    base_config: dict[str, Any],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
    remembered: list[dict[str, Any]],
    evaluate: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]],
    base_objective: float,
    rng: Random,
) -> tuple[list[dict[str, Any]], dict[tuple[tuple[str, Any], ...], dict[str, Any]]]:
    priors = _history_posteriors(base_config, remembered, dimensions, group_totals)
    rows: list[dict[str, Any]] = []
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for dimension in dimensions:
        for arm, direction, config in _neighbors(base_config, dimension, dimensions, group_totals):
            report, robust, objective = evaluate(config)
            delta = float(objective) - float(base_objective)
            alpha, beta = priors.get(arm, (1.0, 1.0))
            prior_mean = alpha / (alpha + beta)
            sampled = rng.betavariate(alpha, beta)
            local_signal = max(0.05, min(0.95, 0.5 + delta / 0.04))
            routing_score = 0.74 * local_signal + 0.26 * sampled
            entry = {
                "arm": arm,
                "field": dimension.name,
                "kind": dimension.kind,
                "direction": direction,
                "objective": round(float(objective), 7),
                "objective_delta": round(delta, 7),
                "prior_mean": round(prior_mean, 4),
                "posterior_sample": round(sampled, 4),
                "routing_score": round(routing_score, 5),
                "config": config,
                "report": report,
                "robustness": robust,
            }
            rows.append(entry)
            cache[_config_key(config)] = {
                "config": config,
                "report": report,
                "robustness": robust,
                "objective": round(float(objective), 7),
                "generation": 0,
                "source": "response_surface",
            }
    rows.sort(
        key=lambda row: (
            -float(row["routing_score"]),
            -float(row["objective_delta"]),
            str(row["arm"]),
        )
    )
    return rows, cache


def _surface_seeds(
    *,
    base_config: dict[str, Any],
    surface: list[dict[str, Any]],
    remembered: list[dict[str, Any]],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
    rng: Random,
) -> tuple[list[dict[str, Any]], bool]:
    population = [dict(row["config"]) for row in surface[: min(6, len(surface))]]

    positive = [row for row in surface if float(row["objective_delta"]) > 0][:4]
    if positive:
        combined = dict(base_config)
        used_fields: set[str] = set()
        for row in positive:
            field = str(row["field"])
            if field in used_fields:
                continue
            used_fields.add(field)
            combined[field] = row["config"][field]
        population.append(_project(combined, dimensions, group_totals))

    base_keys = set(base_config)
    for memory in _trusted_memory_rows(remembered):
        config = memory["config"]
        merged = {key: config.get(key, base_config[key]) for key in base_keys}
        try:
            population.append(_project(merged, dimensions, group_totals))
        except (TypeError, ValueError, KeyError):
            # Corrupted/legacy memory must never crash a new evolution run.
            continue

    best_local = max((float(row["objective_delta"]) for row in surface), default=-1.0)
    basin_jump = best_local < 0.001
    attempts = 0
    while len(population) < POPULATION_SIZE and attempts < POPULATION_SIZE * 8:
        attempts += 1
        population.append(
            _mutate_config(
                base_config,
                dimensions=dimensions,
                group_totals=group_totals,
                rng=rng,
                scale=1.8 if basin_jump else 0.85,
            )
        )
        population = _unique_configs(population)
    return population[:POPULATION_SIZE], basin_jump


def _quality_diversity_archive(
    base_config: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    dimensions: list[EvolutionDimension],
) -> list[dict[str, Any]]:
    archive: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in rows:
        signature = _config_signature(base_config, row["config"], dimensions)
        current = archive.get(signature)
        if current is None or float(row["objective"]) > float(current["objective"]):
            archive[signature] = {**row, "mutation_signature": list(signature)}
    return sorted(
        archive.values(),
        key=lambda row: (-float(row["objective"]), tuple(row["mutation_signature"])),
    )


def _evolution_loop(
    *,
    base_config: dict[str, Any],
    population: list[dict[str, Any]],
    dimensions: list[EvolutionDimension],
    group_totals: dict[str, float],
    evaluate: Callable[[dict[str, Any]], tuple[dict[str, Any], dict[str, float], float]],
    rng: Random,
    cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evaluated = dict(cache or {})
    population = _unique_configs(population)[:POPULATION_SIZE]
    for generation in range(MAX_GENERATIONS):
        generation_rows = []
        for config in population:
            try:
                key = _config_key(config)
                if key not in evaluated:
                    report, robust, objective = evaluate(config)
                    evaluated[key] = {
                        "config": config,
                        "report": report,
                        "robustness": robust,
                        "objective": round(float(objective), 7),
                        "generation": generation + 1,
                        "source": "population",
                    }
                generation_rows.append(evaluated[key])
            except (TypeError, ValueError, KeyError):
                continue
        if not generation_rows or generation + 1 >= MAX_GENERATIONS:
            break

        archive = _quality_diversity_archive(base_config, evaluated.values(), dimensions)
        parents = archive[: min(5, len(archive))]
        population = [dict(row["config"]) for row in parents]
        attempts = 0
        while len(population) < POPULATION_SIZE and attempts < POPULATION_SIZE * 8:
            attempts += 1
            parent = parents[rng.randrange(len(parents))]["config"] if parents else base_config
            population.append(
                _mutate_config(
                    parent,
                    dimensions=dimensions,
                    group_totals=group_totals,
                    rng=rng,
                    scale=0.72,
                )
            )
            population = _unique_configs(population)

    rows = sorted(
        evaluated.values(),
        key=lambda row: (-float(row["objective"]), _config_key(row["config"])),
    )
    return rows, _quality_diversity_archive(base_config, rows, dimensions)


def _audit_search_config(
    catalog: Catalog,
    current: SearchEngine,
    labels: list[Any],
    config: SearchConfig,
) -> dict[str, Any]:
    engine = current.with_config(config)
    details = []
    for label in labels:
        ranked = [row["id"] for row in engine.search(label.query, limit=10)]
        relevant = set(label.relevant)
        details.append(
            {
                "query": label.query,
                "recall": recall_at_k(ranked, relevant),
                "mrr": reciprocal_rank(ranked, relevant),
                "ndcg": ndcg_at_k(ranked, relevant),
                "top": ranked[:3],
            }
        )
    if not details:
        return {
            "queries": 0,
            "available_queries": 0,
            "sampled": False,
            "quality": 0.0,
            "recall": 0.0,
            "mrr": 0.0,
            "details": [],
        }
    return {
        "queries": len(details),
        "available_queries": len(labels),
        "sampled": False,
        "quality": round(mean(row["ndcg"] for row in details), 4),
        "recall": round(mean(row["recall"] for row in details), 4),
        "mrr": round(mean(row["mrr"] for row in details), 4),
        "details": details,
    }


def _audit_recommend_config(
    catalog: Catalog,
    current: RecommendationEngine,
    users: list[str],
    config: RecommendConfig,
    *,
    slice_key: str,
) -> dict[str, Any]:
    engine = current.with_config(config)
    cold = audit_cold_start(catalog, engine, slice_key=slice_key, samples=3)
    if not users:
        return {
            "users": 0,
            "available_users": 0,
            "sampled": False,
            "quality": 0.0,
            "coverage": 0.0,
            "diversity": 0.0,
            "freshness": 0.0,
            "novelty": 0.0,
            "cold_start_quality": cold["quality"],
            "cold_start_samples": cold["samples"],
            "details": [],
        }

    exposed: set[str] = set()
    diversities: list[float] = []
    freshness_values: list[float] = []
    novelty_values: list[float] = []
    details = []
    for user in users:
        result = engine.recommend(user, limit=8)
        exposed.update(row["id"] for row in result)
        categories = [category for row in result for category in row.get("categories", [])]
        diversity = len(set(categories)) / max(1, len(categories))
        freshness = mean([row["freshness"] for row in result]) if result else 0.0
        novelty = mean([row["signals"]["novelty"] for row in result]) if result else 0.0
        diversities.append(diversity)
        freshness_values.append(freshness)
        novelty_values.append(novelty)
        details.append(
            {
                "user_id": user,
                "diversity": round(diversity, 4),
                "freshness": round(freshness, 4),
                "top": [row["id"] for row in result[:4]],
            }
        )

    eligible_count = sum(1 for item in catalog.items if item.eligible)
    coverage = len(exposed) / max(1, eligible_count)
    diversity = mean(diversities)
    freshness = mean(freshness_values)
    novelty = mean(novelty_values)
    quality = (
        0.41 * coverage
        + 0.23 * diversity
        + 0.18 * freshness
        + 0.08 * novelty
        + 0.10 * float(cold["quality"])
    )
    return {
        "users": len(users),
        "available_users": len(users),
        "sampled": False,
        "quality": round(quality, 4),
        "coverage": round(coverage, 4),
        "diversity": round(diversity, 4),
        "freshness": round(freshness, 4),
        "novelty": round(novelty, 4),
        "cold_start_quality": cold["quality"],
        "cold_start_samples": cold["samples"],
        "details": details,
    }


def _search_robustness(reference: dict[str, Any], trial: dict[str, Any]) -> dict[str, float]:
    base = {row["query"]: row for row in reference.get("details", [])}
    deltas = [
        float(row["ndcg"]) - float(base[row["query"]]["ndcg"])
        for row in trial.get("details", [])
        if row["query"] in base
    ]
    if not deltas:
        return {"worse_share": 1.0, "worst_delta": -1.0, "mean_delta": 0.0}
    return {
        "worse_share": round(sum(1 for value in deltas if value < -0.02) / len(deltas), 4),
        "worst_delta": round(min(deltas), 4),
        "mean_delta": round(mean(deltas), 4),
    }


def _recommend_robustness(reference: dict[str, Any], trial: dict[str, Any]) -> dict[str, float]:
    base = {row["user_id"]: row for row in reference.get("details", [])}
    deltas = []
    for row in trial.get("details", []):
        previous = base.get(row["user_id"])
        if not previous:
            continue
        utility = 0.55 * float(row["diversity"]) + 0.45 * float(row["freshness"])
        old = 0.55 * float(previous["diversity"]) + 0.45 * float(previous["freshness"])
        deltas.append(utility - old)
    if not deltas:
        return {"worse_share": 1.0, "worst_delta": -1.0, "mean_delta": 0.0}
    return {
        "worse_share": round(sum(1 for value in deltas if value < -0.03) / len(deltas), 4),
        "worst_delta": round(min(deltas), 4),
        "mean_delta": round(mean(deltas), 4),
    }


def _search_objective(report: dict[str, Any], robust: dict[str, float] | None = None) -> float:
    robust = robust or {"worse_share": 0.0, "worst_delta": 0.0}
    return (
        float(report.get("quality", 0.0))
        + 0.08 * float(report.get("recall", 0.0))
        - 0.035 * float(robust.get("worse_share", 0.0))
        + 0.015 * min(0.0, float(robust.get("worst_delta", 0.0)))
    )


def _recommend_objective(report: dict[str, Any], robust: dict[str, float] | None = None) -> float:
    robust = robust or {"worse_share": 0.0, "worst_delta": 0.0}
    return (
        float(report.get("quality", 0.0))
        + 0.05 * float(report.get("freshness", 0.0))
        + 0.03 * float(report.get("diversity", 0.0))
        + 0.04 * float(report.get("cold_start_quality", 0.0))
        - 0.03 * float(robust.get("worse_share", 0.0))
        + 0.01 * min(0.0, float(robust.get("worst_delta", 0.0)))
    )


def _recommend_gates(
    *,
    users: int,
    q_delta: float,
    cov_delta: float,
    fresh_delta: float,
    div_delta: float,
    cold_delta: float,
    discovery_delta: float,
    robust: dict[str, float],
    holdout_available: bool,
    holdout_q_delta: float,
    holdout_cov_delta: float,
    holdout_cold_delta: float,
    holdout_robust: dict[str, float] | None,
) -> tuple[bool, bool]:
    """Apply explicit warm + cold safety and trust gates."""

    safe = (
        users >= MIN_RECOMMEND_EVIDENCE
        and q_delta >= -0.003
        and cov_delta >= -0.02
        and fresh_delta >= -0.012
        and cold_delta >= -0.03
        and robust["worse_share"] <= 0.40
        and robust["worst_delta"] >= -0.30
        and (
            not holdout_available
            or (
                holdout_q_delta >= -0.008
                and holdout_cov_delta >= -0.06
                and holdout_cold_delta >= -0.02
            )
        )
        and (not holdout_robust or holdout_robust["worst_delta"] >= -0.35)
    )
    trusted = (
        safe
        and holdout_available
        and discovery_delta >= 0.001
        and holdout_cold_delta >= -0.005
        and (
            q_delta > 0.0005
            or fresh_delta > 0.002
            or div_delta > 0.002
            or cold_delta > 0.002
        )
    )
    return safe, trusted


def _evolution_metadata(
    *,
    dimensions: list[EvolutionDimension],
    surface: list[dict[str, Any]],
    best: dict[str, Any],
    base_config: dict[str, Any],
    archive: list[dict[str, Any]],
    basin_jump: bool,
    remembered: list[dict[str, Any]],
) -> dict[str, Any]:
    capability_dimensions = [dimension for dimension in dimensions if dimension.kind == "capability"]
    return {
        "method": "mixed_genome_response_surface",
        "hardening_version": 2,
        "router": "posterior_guided_mixed_arms",
        "domain_driven": True,
        "handwritten_mutation_recipes": False,
        "central_capability_preferences": False,
        "schema": [dimension.dict() for dimension in dimensions],
        "continuous_dimensions": sum(1 for dimension in dimensions if dimension.kind == "continuous"),
        "capability_dimensions": len(capability_dimensions),
        "response_surface": [
            {
                "arm": row["arm"],
                "field": row["field"],
                "kind": row["kind"],
                "direction": row["direction"],
                "objective_delta": row["objective_delta"],
                "prior_mean": row["prior_mean"],
                "posterior_sample": row["posterior_sample"],
                "routing_score": row["routing_score"],
            }
            for row in surface
        ],
        "semantic_gradient": [
            {
                "arm": row["arm"],
                "kind": row["kind"],
                "objective_delta": row["objective_delta"],
            }
            for row in sorted(surface, key=lambda item: -abs(float(item["objective_delta"])))[:8]
        ],
        "selected_signature": list(_config_signature(base_config, best["config"], dimensions)),
        "selected_capabilities": {
            dimension.name: str(best["config"][dimension.name])
            for dimension in capability_dimensions
        },
        "basin_jump": basin_jump,
        "archive_size": len(archive),
        "archive": [
            {"signature": row["mutation_signature"], "objective": row["objective"]}
            for row in archive[:10]
        ],
        "remembered_trusted_strategies": len(list(_trusted_memory_rows(remembered))),
    }


def evolve_search(
    catalog: Catalog,
    current: SearchEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remembered = remembered or []
    labels = _stable_limit(list(catalog.query_labels), lambda row: row.query)
    discovery_labels, holdout_labels = _stable_split(labels, lambda row: row.query)
    base_config = asdict(current.config)
    dimensions, group_totals = _evolution_schema(current.config)

    reference = _audit_search_config(catalog, current, labels, current.config)
    reference_discovery = _audit_search_config(catalog, current, discovery_labels, current.config)
    reference_holdout = (
        _audit_search_config(catalog, current, holdout_labels, current.config)
        if holdout_labels
        else None
    )
    if int(reference.get("queries", 0)) < MIN_SEARCH_EVIDENCE:
        return _not_ready_search(reference, base_config, dimensions)

    def evaluate(config: dict[str, Any]):
        cfg = normalize_strategy_config(SearchConfig(**config))
        report = _audit_search_config(catalog, current, discovery_labels, cfg)
        robust = _search_robustness(reference_discovery, report)
        return report, robust, _search_objective(report, robust)

    rng = Random(_seed(catalog, "search"))
    base_objective = _search_objective(reference_discovery)
    surface, cache = _response_surface(
        base_config=base_config,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=remembered,
        evaluate=evaluate,
        base_objective=base_objective,
        rng=rng,
    )
    population, basin_jump = _surface_seeds(
        base_config=base_config,
        surface=surface,
        remembered=remembered,
        dimensions=dimensions,
        group_totals=group_totals,
        rng=rng,
    )
    rows, archive = _evolution_loop(
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
        evaluate=evaluate,
        rng=rng,
        cache=cache,
    )
    if not rows:
        return _not_ready_search(reference, base_config, dimensions)

    best = rows[0]
    candidate_config = normalize_strategy_config(SearchConfig(**best["config"]))
    best["config"] = asdict(candidate_config)
    trial = _audit_search_config(catalog, current, labels, candidate_config)
    robust = _search_robustness(reference, trial)
    holdout = (
        _audit_search_config(catalog, current, holdout_labels, candidate_config)
        if holdout_labels
        else None
    )
    holdout_robust = (
        _search_robustness(reference_holdout, holdout)
        if holdout and reference_holdout
        else None
    )
    quality_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    recall_delta = float(trial.get("recall", 0.0)) - float(reference.get("recall", 0.0))
    discovery_delta = float(best["objective"]) - base_objective
    holdout_quality_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout
        else 0.0
    )
    holdout_recall_delta = (
        float(holdout.get("recall", 0.0)) - float(reference_holdout.get("recall", 0.0))
        if holdout and reference_holdout
        else 0.0
    )
    safe = (
        trial.get("queries", 0) >= MIN_SEARCH_EVIDENCE
        and quality_delta >= -0.002
        and recall_delta >= -0.01
        and robust["worse_share"] <= 0.34
        and robust["worst_delta"] >= -0.35
        and (
            not holdout
            or (holdout_quality_delta >= -0.005 and holdout_recall_delta >= -0.02)
        )
        and (not holdout_robust or holdout_robust["worst_delta"] >= -0.45)
    )
    trusted = (
        safe
        and bool(holdout_labels)
        and discovery_delta >= 0.001
        and (quality_delta > 0.0005 or recall_delta > 0.002)
    )
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {"quality": round(quality_delta, 4), "recall": round(recall_delta, 4)},
        "evaluation_ready": True,
        "safe_to_try": safe,
        "trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "validation": {
            "discovery": {"samples": len(discovery_labels), "objective_delta": round(discovery_delta, 5)},
            "holdout": {
                "samples": len(holdout_labels),
                "independent": bool(holdout_labels),
                "quality_delta": round(holdout_quality_delta, 4),
                "recall_delta": round(holdout_recall_delta, 4),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "quality": row["report"].get("quality", 0.0),
                "recall": row["report"].get("recall", 0.0),
                "worse_share": row["robustness"].get("worse_share", 0.0),
                "signature": list(_config_signature(base_config, row["config"], dimensions)),
            }
            for row in rows[:3]
        ],
        "evolution": _evolution_metadata(
            dimensions=dimensions,
            surface=surface,
            best=best,
            base_config=base_config,
            archive=archive,
            basin_jump=basin_jump,
            remembered=remembered,
        ),
    }


def evolve_recommend(
    catalog: Catalog,
    current: RecommendationEngine,
    *,
    remembered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    remembered = remembered or []
    users = _stable_limit(current.known_users(), lambda user: user)
    discovery_users, holdout_users = _stable_split(users, lambda user: user)
    base_config = asdict(current.config)
    dimensions, group_totals = _evolution_schema(current.config)

    reference = _audit_recommend_config(catalog, current, users, current.config, slice_key="full")
    reference_discovery = _audit_recommend_config(
        catalog, current, discovery_users, current.config, slice_key="discovery"
    )
    reference_holdout = (
        _audit_recommend_config(catalog, current, holdout_users, current.config, slice_key="holdout")
        if holdout_users
        else None
    )
    if int(reference.get("users", 0)) < MIN_RECOMMEND_EVIDENCE:
        return _not_ready_recommend(reference, base_config, dimensions)

    def evaluate(config: dict[str, Any]):
        cfg = normalize_strategy_config(RecommendConfig(**config))
        report = _audit_recommend_config(catalog, current, discovery_users, cfg, slice_key="discovery")
        robust = _recommend_robustness(reference_discovery, report)
        return report, robust, _recommend_objective(report, robust)

    rng = Random(_seed(catalog, "recommend"))
    base_objective = _recommend_objective(reference_discovery)
    surface, cache = _response_surface(
        base_config=base_config,
        dimensions=dimensions,
        group_totals=group_totals,
        remembered=remembered,
        evaluate=evaluate,
        base_objective=base_objective,
        rng=rng,
    )
    population, basin_jump = _surface_seeds(
        base_config=base_config,
        surface=surface,
        remembered=remembered,
        dimensions=dimensions,
        group_totals=group_totals,
        rng=rng,
    )
    rows, archive = _evolution_loop(
        base_config=base_config,
        population=population,
        dimensions=dimensions,
        group_totals=group_totals,
        evaluate=evaluate,
        rng=rng,
        cache=cache,
    )
    if not rows:
        return _not_ready_recommend(reference, base_config, dimensions)

    best = rows[0]
    candidate_config = normalize_strategy_config(RecommendConfig(**best["config"]))
    best["config"] = asdict(candidate_config)
    trial = _audit_recommend_config(catalog, current, users, candidate_config, slice_key="full")
    robust = _recommend_robustness(reference, trial)
    holdout = (
        _audit_recommend_config(catalog, current, holdout_users, candidate_config, slice_key="holdout")
        if holdout_users
        else None
    )
    holdout_robust = (
        _recommend_robustness(reference_holdout, holdout)
        if holdout and reference_holdout
        else None
    )

    q_delta = float(trial.get("quality", 0.0)) - float(reference.get("quality", 0.0))
    fresh_delta = float(trial.get("freshness", 0.0)) - float(reference.get("freshness", 0.0))
    cov_delta = float(trial.get("coverage", 0.0)) - float(reference.get("coverage", 0.0))
    div_delta = float(trial.get("diversity", 0.0)) - float(reference.get("diversity", 0.0))
    cold_delta = float(trial.get("cold_start_quality", 0.0)) - float(reference.get("cold_start_quality", 0.0))
    discovery_delta = float(best["objective"]) - base_objective
    holdout_q_delta = (
        float(holdout.get("quality", 0.0)) - float(reference_holdout.get("quality", 0.0))
        if holdout and reference_holdout
        else 0.0
    )
    holdout_cov_delta = (
        float(holdout.get("coverage", 0.0)) - float(reference_holdout.get("coverage", 0.0))
        if holdout and reference_holdout
        else 0.0
    )
    holdout_cold_delta = (
        float(holdout.get("cold_start_quality", 0.0))
        - float(reference_holdout.get("cold_start_quality", 0.0))
        if holdout and reference_holdout
        else 0.0
    )
    safe, trusted = _recommend_gates(
        users=int(trial.get("users", 0)),
        q_delta=q_delta,
        cov_delta=cov_delta,
        fresh_delta=fresh_delta,
        div_delta=div_delta,
        cold_delta=cold_delta,
        discovery_delta=discovery_delta,
        robust=robust,
        holdout_available=bool(holdout_users),
        holdout_q_delta=holdout_q_delta,
        holdout_cov_delta=holdout_cov_delta,
        holdout_cold_delta=holdout_cold_delta,
        holdout_robust=holdout_robust,
    )
    return {
        "reference": reference,
        "candidate": trial,
        "delta": {
            "quality": round(q_delta, 4),
            "freshness": round(fresh_delta, 4),
            "coverage": round(cov_delta, 4),
            "diversity": round(div_delta, 4),
            "cold_start_quality": round(cold_delta, 4),
        },
        "evaluation_ready": True,
        "safe_to_try": safe,
        "trusted": trusted,
        "candidate_config": best["config"],
        "candidate_count": len(rows),
        "generations": MAX_GENERATIONS,
        "robustness": robust,
        "objective_delta": round(discovery_delta, 5),
        "validation": {
            "discovery": {
                "samples": len(discovery_users),
                "objective_delta": round(discovery_delta, 5),
                "cold_start_samples": int(reference_discovery.get("cold_start_samples", 0)),
            },
            "holdout": {
                "samples": len(holdout_users),
                "independent": bool(holdout_users),
                "quality_delta": round(holdout_q_delta, 4),
                "coverage_delta": round(holdout_cov_delta, 4),
                "cold_start_quality_delta": round(holdout_cold_delta, 4),
                "cold_start_samples": int((holdout or {}).get("cold_start_samples", 0)),
                "robustness": holdout_robust,
            },
        },
        "top_candidates": [
            {
                "objective": row["objective"],
                "quality": row["report"].get("quality", 0.0),
                "coverage": row["report"].get("coverage", 0.0),
                "freshness": row["report"].get("freshness", 0.0),
                "cold_start_quality": row["report"].get("cold_start_quality", 0.0),
                "worse_share": row["robustness"].get("worse_share", 0.0),
                "signature": list(_config_signature(base_config, row["config"], dimensions)),
            }
            for row in rows[:3]
        ],
        "evolution": _evolution_metadata(
            dimensions=dimensions,
            surface=surface,
            best=best,
            base_config=base_config,
            archive=archive,
            basin_jump=basin_jump,
            remembered=remembered,
        ),
    }


def _not_ready_search(
    reference: dict[str, Any],
    base_config: dict[str, Any],
    dimensions: list[EvolutionDimension] | None = None,
) -> dict[str, Any]:
    return {
        "reference": reference,
        "candidate": reference,
        "delta": {"quality": 0.0, "recall": 0.0},
        "evaluation_ready": False,
        "safe_to_try": False,
        "trusted": False,
        "candidate_config": base_config,
        "candidate_count": 0,
        "generations": 0,
        "robustness": {"worse_share": 0.0, "worst_delta": 0.0, "mean_delta": 0.0},
        "objective_delta": 0.0,
        "validation": {"discovery": {"samples": 0}, "holdout": {"samples": 0}},
        "evolution": {
            "method": "mixed_genome_response_surface",
            "hardening_version": 2,
            "router": "posterior_guided_mixed_arms",
            "domain_driven": True,
            "handwritten_mutation_recipes": False,
            "central_capability_preferences": False,
            "schema": [dimension.dict() for dimension in (dimensions or [])],
            "response_surface": [],
            "reason": "insufficient_evaluation_evidence",
        },
    }


def _not_ready_recommend(
    reference: dict[str, Any],
    base_config: dict[str, Any],
    dimensions: list[EvolutionDimension] | None = None,
) -> dict[str, Any]:
    return {
        "reference": reference,
        "candidate": reference,
        "delta": {
            "quality": 0.0,
            "freshness": 0.0,
            "coverage": 0.0,
            "diversity": 0.0,
            "cold_start_quality": 0.0,
        },
        "evaluation_ready": False,
        "safe_to_try": False,
        "trusted": False,
        "candidate_config": base_config,
        "candidate_count": 0,
        "generations": 0,
        "robustness": {"worse_share": 0.0, "worst_delta": 0.0, "mean_delta": 0.0},
        "objective_delta": 0.0,
        "validation": {"discovery": {"samples": 0}, "holdout": {"samples": 0}},
        "evolution": {
            "method": "mixed_genome_response_surface",
            "hardening_version": 2,
            "router": "posterior_guided_mixed_arms",
            "domain_driven": True,
            "handwritten_mutation_recipes": False,
            "central_capability_preferences": False,
            "schema": [dimension.dict() for dimension in (dimensions or [])],
            "response_surface": [],
            "reason": "insufficient_evaluation_evidence",
        },
    }


__all__ = [
    "EvolutionDimension",
    "_evolution_schema",
    "_history_posteriors",
    "_perturb",
    "_project",
    "_recommend_gates",
    "_stable_split",
    "evolve_search",
    "evolve_recommend",
]
