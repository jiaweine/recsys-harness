from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import blake2b
from importlib.util import find_spec
import json
from math import exp, isfinite, log, sqrt, tanh
from statistics import mean, pstdev
from typing import Any, Iterable, Iterator, Mapping, Sequence


_ROUTER_HISTORY: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
    "xushu_optimizer_meta_history", default=()
)
_FIXED_BACKENDS = ("native", "optuna", "optuna_motpe", "qlognehvi")
_BENCHMARK_PRIOR_SOURCE = "equal_budget_mixed_constrained"
_MIN_LANDSCAPE_ROWS = 4


def _bucket(value: float | None, *, low: float, high: float, labels: tuple[str, str, str]) -> str:
    if value is None:
        return "unknown"
    if value < low:
        return labels[0]
    if value < high:
        return labels[1]
    return labels[2]


@dataclass(frozen=True, slots=True)
class OptimizerLandscapeDescriptors:
    """Zero-new-evaluation geometry summarized from already observed rows only."""

    rows: int = 0
    scored_rows: int = 0
    relative_score_span: float | None = None
    local_response_roughness: float | None = None
    local_slope_dispersion: float | None = None
    feasible_density: float | None = None
    categorical_response_separation: float | None = None

    @property
    def informative(self) -> bool:
        return self.scored_rows >= _MIN_LANDSCAPE_ROWS and any(
            value is not None
            for value in (
                self.relative_score_span,
                self.local_response_roughness,
                self.local_slope_dispersion,
                self.feasible_density,
                self.categorical_response_separation,
            )
        )

    @property
    def rows_bucket(self) -> str:
        if self.scored_rows <= 0:
            return "none"
        if self.scored_rows < _MIN_LANDSCAPE_ROWS:
            return "sparse"
        if self.scored_rows < 8:
            return "moderate"
        return "rich"

    @property
    def score_span_bucket(self) -> str:
        return _bucket(
            self.relative_score_span,
            low=1.0,
            high=2.0,
            labels=("compact", "broad", "extreme"),
        )

    @property
    def roughness_bucket(self) -> str:
        return _bucket(
            self.local_response_roughness,
            low=0.45,
            high=0.78,
            labels=("smooth", "mixed", "rugged"),
        )

    @property
    def slope_dispersion_bucket(self) -> str:
        return _bucket(
            self.local_slope_dispersion,
            low=0.35,
            high=0.50,
            labels=("stable", "variable", "volatile"),
        )

    @property
    def feasible_density_bucket(self) -> str:
        return _bucket(
            self.feasible_density,
            low=0.35,
            high=0.75,
            labels=("sparse", "mixed", "dense"),
        )

    @property
    def categorical_response_bucket(self) -> str:
        return _bucket(
            self.categorical_response_separation,
            low=0.20,
            high=0.55,
            labels=("weak", "moderate", "strong"),
        )

    def identity_dict(self) -> dict[str, Any]:
        return {
            "rows_bucket": self.rows_bucket,
            "score_span_bucket": self.score_span_bucket,
            "roughness_bucket": self.roughness_bucket,
            "slope_dispersion_bucket": self.slope_dispersion_bucket,
            "feasible_density_bucket": self.feasible_density_bucket,
            "categorical_response_bucket": self.categorical_response_bucket,
        }

    def to_dict(self) -> dict[str, Any]:
        def rendered(value: float | None) -> float | None:
            return None if value is None else round(float(value), 6)

        return {
            "source": "preobserved_rows_only",
            "new_evaluator_calls": 0,
            "rows": int(self.rows),
            "scored_rows": int(self.scored_rows),
            "informative": self.informative,
            "relative_score_span": rendered(self.relative_score_span),
            "local_response_roughness": rendered(self.local_response_roughness),
            "local_slope_dispersion": rendered(self.local_slope_dispersion),
            "feasible_density": rendered(self.feasible_density),
            "categorical_response_separation": rendered(self.categorical_response_separation),
            **self.identity_dict(),
        }


def _observation_rows(
    observations: Sequence[Mapping[str, Any]] | Mapping[Any, Any] | None,
) -> list[Mapping[str, Any]]:
    if observations is None:
        return []
    raw: Iterable[Any]
    if isinstance(observations, Mapping):
        raw = observations.values()
    else:
        raw = observations
    return [row for row in raw if isinstance(row, Mapping)]


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _observation_score(row: Mapping[str, Any]) -> float | None:
    for key in ("objective", "score"):
        if key in row:
            value = _finite_float(row.get(key))
            if value is not None:
                return value
    report = row.get("report")
    if isinstance(report, Mapping):
        for key in ("business_reward", "quality"):
            value = _finite_float(report.get(key))
            if value is not None:
                return value
    return None


def _normalized_distance(left: Mapping[str, Any], right: Mapping[str, Any], dimensions: Sequence[Any]) -> float | None:
    parts: list[float] = []
    for dimension in dimensions:
        name = str(getattr(dimension, "name", "") or "")
        if not name or name not in left or name not in right:
            return None
        if str(getattr(dimension, "kind", "")) == "continuous":
            left_value = _finite_float(left.get(name))
            right_value = _finite_float(right.get(name))
            if left_value is None or right_value is None:
                return None
            low = _finite_float(getattr(dimension, "low", None))
            high = _finite_float(getattr(dimension, "high", None))
            scale = abs(high - low) if low is not None and high is not None and high != low else 1.0
            parts.append(((left_value - right_value) / max(scale, 1e-12)) ** 2)
        else:
            parts.append(0.0 if str(left.get(name)) == str(right.get(name)) else 1.0)
    if not parts:
        return None
    return sqrt(sum(parts) / len(parts))


def describe_optimizer_landscape(
    *,
    dimensions: Sequence[Any],
    observations: Sequence[Mapping[str, Any]] | Mapping[Any, Any] | None,
) -> OptimizerLandscapeDescriptors:
    """Summarize already-evaluated geometry without spending optimizer budget.

    Rows may come from a shared initial-design cache or durable trusted strategy
    memory. Missing feasibility is kept unknown rather than inferred from success-
    only memory, so the router cannot manufacture a dense feasible region.
    """

    raw_rows = _observation_rows(observations)
    scored: list[tuple[Mapping[str, Any], float]] = []
    feasibility: list[bool] = []
    feasibility_complete = True
    for row in raw_rows:
        config = row.get("config")
        score = _observation_score(row)
        if not isinstance(config, Mapping) or score is None:
            continue
        if any(str(getattr(dimension, "name", "") or "") not in config for dimension in dimensions):
            continue
        scored.append((config, score))
        feasible = row.get("feasible")
        if isinstance(feasible, bool):
            feasibility.append(feasible)
        else:
            feasibility_complete = False

    if len(scored) < _MIN_LANDSCAPE_ROWS:
        return OptimizerLandscapeDescriptors(rows=len(raw_rows), scored_rows=len(scored))

    scores = [score for _, score in scored]
    score_mean = mean(scores)
    score_low = min(scores)
    score_high = max(scores)
    score_span = max(0.0, score_high - score_low)
    relative_span = score_span / max(0.05, abs(score_mean))

    if score_span <= 1e-12:
        normalized_scores = [0.0 for _ in scores]
    else:
        normalized_scores = [(score - score_low) / score_span for score in scores]

    local_slopes: list[float] = []
    for index, (config, _) in enumerate(scored):
        nearest: tuple[float, int] | None = None
        for other_index, (other_config, _) in enumerate(scored):
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
        local_slopes.append(
            abs(normalized_scores[index] - normalized_scores[other_index]) / distance
        )

    roughness: float | None = None
    slope_dispersion: float | None = None
    if local_slopes:
        slope_mean = mean(local_slopes)
        roughness = tanh(slope_mean)
        slope_dispersion = tanh(
            pstdev(local_slopes) / max(0.25, slope_mean + 0.25)
        ) if len(local_slopes) >= 2 else 0.0

    categorical_separations: list[float] = []
    for dimension in dimensions:
        if str(getattr(dimension, "kind", "")) == "continuous":
            continue
        name = str(getattr(dimension, "name", "") or "")
        groups: dict[str, list[float]] = {}
        for (config, _), normalized in zip(scored, normalized_scores):
            groups.setdefault(str(config.get(name)), []).append(normalized)
        if len(groups) < 2:
            continue
        group_means = [mean(values) for values in groups.values() if values]
        if len(group_means) >= 2:
            categorical_separations.append(max(group_means) - min(group_means))

    feasible_density = None
    if feasibility_complete and len(feasibility) == len(scored):
        feasible_density = sum(1 for value in feasibility if value) / len(feasibility)

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


@dataclass(frozen=True, slots=True)
class OptimizerRoutingContext:
    surface: str
    evidence_route: str
    evaluation_budget: int
    warm_start_rows: int
    continuous_dimensions: int
    categorical_dimensions: int
    categorical_cardinality: int
    objective_count: int
    constraint_count: int
    landscape: OptimizerLandscapeDescriptors = OptimizerLandscapeDescriptors()

    @property
    def budget_bucket(self) -> str:
        budget = int(self.evaluation_budget)
        if budget <= 3:
            return "tiny"
        if budget <= 12:
            return "small"
        if budget <= 32:
            return "medium"
        return "large"

    @property
    def categorical_bucket(self) -> str:
        cardinality = int(self.categorical_cardinality)
        if cardinality <= 1:
            return "none"
        if cardinality <= 8:
            return "small"
        if cardinality <= 32:
            return "medium"
        return "large"

    def _base_identity(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "evidence_route": self.evidence_route,
            "evaluation_budget": int(self.evaluation_budget),
            "budget_bucket": self.budget_bucket,
            "warm_start_rows": int(self.warm_start_rows),
            "continuous_dimensions": int(self.continuous_dimensions),
            "categorical_dimensions": int(self.categorical_dimensions),
            "categorical_cardinality": int(self.categorical_cardinality),
            "categorical_bucket": self.categorical_bucket,
            "objective_count": int(self.objective_count),
            "constraint_count": int(self.constraint_count),
        }

    @property
    def context_key(self) -> str:
        identity = self._base_identity()
        if self.landscape.informative:
            identity["landscape"] = self.landscape.identity_dict()
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return blake2b(payload.encode("utf-8"), digest_size=12).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._base_identity(),
            "landscape": self.landscape.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class OptimizerRoutingDecision:
    selected_backend: str
    ranked_backends: tuple[str, ...]
    scores: Mapping[str, float]
    context: OptimizerRoutingContext
    availability: Mapping[str, bool]
    history_rows: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": "cost_aware_contextual_ucb",
            "selected_backend": self.selected_backend,
            "ranked_backends": list(self.ranked_backends),
            "scores": {name: round(float(score), 6) for name, score in self.scores.items()},
            "context": self.context.to_dict(),
            "context_key": self.context.context_key,
            "availability": {name: bool(value) for name, value in self.availability.items()},
            "history_rows": int(self.history_rows),
            "benchmark_prior_source": _BENCHMARK_PRIOR_SOURCE,
            "landscape_evidence": "preobserved_only_zero_new_evaluations",
            "authority": "optimizer_selection_only",
        }


def _module_available(name: str) -> bool:
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def optimizer_dependency_availability() -> dict[str, bool]:
    optuna = _module_available("optuna")
    qlog = _module_available("botorch") and _module_available("torch") and _module_available("gpytorch")
    return {
        "native": True,
        "optuna": optuna,
        "optuna_motpe": optuna,
        "qlognehvi": qlog,
    }


def build_routing_context(
    *,
    surface: str,
    evidence_route: str,
    evaluation_budget: int,
    dimensions: Sequence[Any],
    cache: Mapping[Any, Any] | None,
    objective_count: int,
    constraint_count: int,
    landscape_observations: Sequence[Mapping[str, Any]] | Mapping[Any, Any] | None = None,
) -> OptimizerRoutingContext:
    continuous = 0
    categorical = 0
    cardinality = 1
    for dimension in dimensions:
        if str(getattr(dimension, "kind", "")) == "continuous":
            continuous += 1
            continue
        categorical += 1
        choices = tuple(getattr(dimension, "choices", ()) or ())
        cardinality *= max(1, len(choices))
        cardinality = min(cardinality, 1_000_000)
    return OptimizerRoutingContext(
        surface=str(surface or "unknown"),
        evidence_route=str(evidence_route or "proxy"),
        evaluation_budget=max(0, int(evaluation_budget)),
        warm_start_rows=len(cache or {}),
        continuous_dimensions=continuous,
        categorical_dimensions=categorical,
        categorical_cardinality=cardinality if categorical else 1,
        objective_count=max(1, int(objective_count)),
        constraint_count=max(0, int(constraint_count)),
        landscape=describe_optimizer_landscape(
            dimensions=dimensions,
            observations=landscape_observations,
        ),
    )


def _landscape_prior_adjustment(backend: str, context: OptimizerRoutingContext) -> float:
    landscape = context.landscape
    if not landscape.informative:
        return 0.0

    delta = 0.0
    if context.evaluation_budget <= 12:
        clear_coarse_geometry = (
            landscape.score_span_bucket == "compact"
            and landscape.slope_dispersion_bucket == "stable"
            and landscape.categorical_response_bucket == "strong"
        )
        if clear_coarse_geometry:
            delta += {
                "native": 0.46,
                "optuna": -0.08,
                "optuna_motpe": -0.07,
                "qlognehvi": -0.04,
            }[backend]

        difficult_local_geometry = (
            landscape.score_span_bucket == "extreme"
            or landscape.slope_dispersion_bucket == "volatile"
        )
        if difficult_local_geometry:
            delta += {
                "native": -0.10,
                "optuna": 0.10,
                "optuna_motpe": 0.04,
                "qlognehvi": -0.04,
            }[backend]

    if context.evidence_route != "production" and landscape.roughness_bucket == "rugged":
        delta += {
            "native": 0.00,
            "optuna": 0.02,
            "optuna_motpe": 0.01,
            "qlognehvi": -0.03,
        }[backend]

    if (
        context.evidence_route == "production"
        and context.evaluation_budget >= 13
        and landscape.score_span_bucket == "compact"
        and landscape.slope_dispersion_bucket == "stable"
    ):
        delta += {
            "native": -0.02,
            "optuna": 0.00,
            "optuna_motpe": 0.02,
            "qlognehvi": 0.08,
        }[backend]

    if (
        context.evaluation_budget >= 13
        and context.objective_count >= 2
        and landscape.feasible_density_bucket == "sparse"
    ):
        delta += {
            "native": -0.03,
            "optuna": 0.00,
            "optuna_motpe": 0.04,
            "qlognehvi": 0.04,
        }[backend]
    return delta


def _prior_score(backend: str, context: OptimizerRoutingContext) -> float:
    score = {
        "native": 0.40,
        "optuna": 0.62,
        "optuna_motpe": 0.61,
        "qlognehvi": 0.68,
    }[backend]
    budget = context.evaluation_budget
    if budget <= 3:
        score += {"native": 0.30, "optuna": 0.02, "optuna_motpe": -0.12, "qlognehvi": -0.34}[backend]
    elif budget <= 12:
        # Small budgets rarely amortize Pareto-front machinery in proxy search.
        # Keep scalar TPE as the cost-aware prior unless preobserved geometry or
        # contextual credit proves that another backend earns back its overhead.
        score += {"native": -0.02, "optuna": 0.14, "optuna_motpe": -0.02, "qlognehvi": 0.00}[backend]
    elif budget <= 32:
        score += {"native": -0.08, "optuna": 0.05, "optuna_motpe": 0.07, "qlognehvi": 0.12}[backend]
    else:
        score += {"native": -0.12, "optuna": 0.00, "optuna_motpe": 0.06, "qlognehvi": 0.16}[backend]

    if context.objective_count >= 2:
        score += {"native": -0.03, "optuna": -0.03, "optuna_motpe": 0.09, "qlognehvi": 0.08}[backend]
    if context.constraint_count:
        score += {"native": -0.03, "optuna": -0.01, "optuna_motpe": 0.04, "qlognehvi": 0.12}[backend]

    if context.continuous_dimensions == 0:
        score += {"native": 0.06, "optuna": 0.10, "optuna_motpe": 0.05, "qlognehvi": -0.25}[backend]
    elif context.continuous_dimensions <= 4:
        score += {"native": 0.00, "optuna": 0.03, "optuna_motpe": 0.03, "qlognehvi": 0.04}[backend]
    elif context.continuous_dimensions >= 9:
        score += {"native": -0.04, "optuna": 0.06, "optuna_motpe": 0.04, "qlognehvi": -0.05}[backend]

    if context.categorical_cardinality > 32:
        score += {"native": 0.00, "optuna": 0.08, "optuna_motpe": 0.05, "qlognehvi": -0.08}[backend]
    elif 1 < context.categorical_cardinality <= 8:
        score += {"native": 0.00, "optuna": 0.02, "optuna_motpe": 0.02, "qlognehvi": 0.03}[backend]

    if context.evidence_route == "production":
        score += {"native": -0.02, "optuna": 0.01, "optuna_motpe": 0.03, "qlognehvi": 0.12}[backend]
    else:
        score += {"native": 0.03, "optuna": 0.08, "optuna_motpe": 0.02, "qlognehvi": -0.24}[backend]

    if context.warm_start_rows < 4:
        score += {"native": 0.05, "optuna": 0.03, "optuna_motpe": -0.02, "qlognehvi": -0.18}[backend]
    elif context.warm_start_rows >= 6:
        score += {"native": 0.00, "optuna": 0.02, "optuna_motpe": 0.03, "qlognehvi": 0.05}[backend]
    return score + _landscape_prior_adjustment(backend, context)


def _history_context(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("context")
    if isinstance(raw, Mapping):
        return raw
    raw_json = row.get("context_json")
    if isinstance(raw_json, str):
        try:
            parsed = json.loads(raw_json)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _history_similarity(row: Mapping[str, Any], context: OptimizerRoutingContext) -> float:
    if str(row.get("context_key") or "") == context.context_key:
        return 1.0
    other = _history_context(row)
    if not other:
        return 0.0
    similarity = 0.0
    similarity += 0.20 if str(other.get("surface")) == context.surface else 0.0
    similarity += 0.25 if str(other.get("evidence_route")) == context.evidence_route else 0.0
    similarity += 0.15 if str(other.get("budget_bucket")) == context.budget_bucket else 0.0
    similarity += 0.10 if int(other.get("continuous_dimensions", -1)) == context.continuous_dimensions else 0.0
    similarity += 0.10 if str(other.get("categorical_bucket")) == context.categorical_bucket else 0.0
    similarity += 0.10 if int(other.get("objective_count", -1)) == context.objective_count else 0.0
    similarity += 0.10 if int(other.get("constraint_count", -1)) == context.constraint_count else 0.0

    other_landscape = other.get("landscape")
    if isinstance(other_landscape, Mapping) and context.landscape.informative:
        current_identity = context.landscape.identity_dict()
        known = 0
        matches = 0
        for key, current_value in current_identity.items():
            other_value = str(other_landscape.get(key) or "unknown")
            if current_value == "unknown" or other_value == "unknown":
                continue
            known += 1
            if other_value == current_value:
                matches += 1
        if known >= 2:
            similarity *= 0.35 + 0.65 * (matches / known)
    return similarity if similarity >= 0.50 else 0.0


def _history_adjustment(
    backend: str,
    context: OptimizerRoutingContext,
    history: Sequence[Mapping[str, Any]],
) -> tuple[float, dict[str, float]]:
    weighted_trials = 0.0
    weighted_utility = 0.0
    total_trials = 0.0
    for row in history:
        try:
            trials = max(0.0, float(row.get("trials", 0.0) or 0.0))
        except (TypeError, ValueError):
            continue
        total_trials += trials
        if str(row.get("backend") or "") != backend or trials <= 0.0:
            continue
        similarity = _history_similarity(row, context)
        if similarity <= 0.0:
            continue
        try:
            utility_sum = float(row.get("utility_sum", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        weighted_trials += similarity * trials
        weighted_utility += similarity * utility_sum

    prior_strength = 4.0
    posterior_mean = (0.5 * prior_strength + weighted_utility) / (prior_strength + weighted_trials)
    exploitation = (posterior_mean - 0.5) * 0.60
    exploration = 0.08 * sqrt(log(total_trials + 2.0) / (weighted_trials + prior_strength))
    exploration = min(0.12, exploration)
    return exploitation + exploration, {
        "weighted_trials": weighted_trials,
        "posterior_mean_utility": posterior_mean,
        "ucb_bonus": exploration,
    }


def rank_optimizer_backends(
    context: OptimizerRoutingContext,
    *,
    history: Sequence[Mapping[str, Any]] = (),
    availability: Mapping[str, bool] | None = None,
) -> OptimizerRoutingDecision:
    available = dict(availability or optimizer_dependency_availability())
    available["native"] = True
    scores: dict[str, float] = {}
    for backend in _FIXED_BACKENDS:
        if not available.get(backend, False):
            continue
        history_delta, _ = _history_adjustment(backend, context, history)
        scores[backend] = _prior_score(backend, context) + history_delta
    if not scores:
        scores = {"native": 0.0}
        available["native"] = True
    preference = {name: index for index, name in enumerate(_FIXED_BACKENDS)}
    ranked = tuple(
        sorted(scores, key=lambda name: (-scores[name], preference.get(name, 99), name))
    )
    return OptimizerRoutingDecision(
        selected_backend=ranked[0],
        ranked_backends=ranked,
        scores=scores,
        context=context,
        availability=available,
        history_rows=len(history),
    )


def optimizer_run_utility(
    *,
    initial_best_objective: float | None,
    final_best_objective: float | None,
    new_evaluations: int,
    wall_seconds: float,
    evidence_route: str,
) -> dict[str, Any]:
    if initial_best_objective is None or final_best_objective is None or new_evaluations <= 0:
        return {
            "credit_eligible": False,
            "utility": None,
            "relative_objective_gain": None,
            "relative_gain_per_evaluator_call": None,
        }
    scale = max(0.05, abs(float(initial_best_objective)))
    relative_gain = (float(final_best_objective) - float(initial_best_objective)) / scale
    gain_per_call = relative_gain / max(1, int(new_evaluations))
    quality_component = 0.5 + 0.5 * tanh(5.0 * relative_gain)
    efficiency_component = 0.5 + 0.5 * tanh(12.0 * gain_per_call)
    latency_scale = 30.0 if str(evidence_route) == "production" else 2.0
    latency_component = exp(-max(0.0, float(wall_seconds)) / latency_scale)
    utility = 0.55 * quality_component + 0.30 * efficiency_component + 0.15 * latency_component
    utility = max(0.0, min(1.0, utility))
    return {
        "credit_eligible": True,
        "utility": utility,
        "relative_objective_gain": relative_gain,
        "relative_gain_per_evaluator_call": gain_per_call,
        "quality_component": quality_component,
        "efficiency_component": efficiency_component,
        "latency_component": latency_component,
        "latency_scale_seconds": latency_scale,
    }


@contextmanager
def optimizer_meta_history(rows: Iterable[Mapping[str, Any]] | None) -> Iterator[None]:
    normalized = tuple(dict(row) for row in (rows or ()))
    token = _ROUTER_HISTORY.set(normalized)
    try:
        yield
    finally:
        _ROUTER_HISTORY.reset(token)


def current_optimizer_meta_history() -> tuple[dict[str, Any], ...]:
    return _ROUTER_HISTORY.get()


__all__ = [
    "OptimizerLandscapeDescriptors",
    "OptimizerRoutingContext",
    "OptimizerRoutingDecision",
    "build_routing_context",
    "describe_optimizer_landscape",
    "optimizer_dependency_availability",
    "rank_optimizer_backends",
    "optimizer_run_utility",
    "optimizer_meta_history",
    "current_optimizer_meta_history",
]
