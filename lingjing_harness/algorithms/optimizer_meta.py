from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from hashlib import blake2b
from importlib.util import find_spec
import json
from math import exp, log, sqrt, tanh
from typing import Any, Iterable, Iterator, Mapping, Sequence


_ROUTER_HISTORY: ContextVar[tuple[dict[str, Any], ...]] = ContextVar(
    "xushu_optimizer_meta_history", default=()
)
_FIXED_BACKENDS = ("native", "optuna", "optuna_motpe", "qlognehvi")
_BENCHMARK_PRIOR_SOURCE = "equal_budget_mixed_constrained"


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

    @property
    def context_key(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return blake2b(payload.encode("utf-8"), digest_size=12).hexdigest()

    def to_dict(self) -> dict[str, Any]:
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
    )


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
        # Keep scalar TPE as the cost-aware prior unless contextual credit proves
        # that a multi-objective backend consistently earns back that overhead.
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
    return score


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
    "OptimizerRoutingContext",
    "OptimizerRoutingDecision",
    "build_routing_context",
    "optimizer_dependency_availability",
    "rank_optimizer_backends",
    "optimizer_run_utility",
    "optimizer_meta_history",
    "current_optimizer_meta_history",
]
