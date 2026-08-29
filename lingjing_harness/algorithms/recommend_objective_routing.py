from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from typing import Any, Iterator

from lingjing_harness.domain import Catalog
from . import evolution_core as core
from .recommend import RecommendConfig, RecommendationEngine
from .recommend_validation import PreparedRecommendRelevance, prepare_recommend_relevance


MIN_RELEVANCE_OBJECTIVE_EVIDENCE = 2 * core.MIN_RECOMMEND_EVIDENCE

_ORIGINAL_RECOMMEND_OBJECTIVE = core._recommend_objective
_ACTIVE_SCOPE: ContextVar[RecommendationObjectiveScope | None] = ContextVar(
    "xushu_recommend_objective_scope",
    default=None,
)
_ACTIVE_CONFIG: ContextVar[RecommendConfig | None] = ContextVar(
    "xushu_recommend_objective_config",
    default=None,
)
_INSTALLED = False


def _config_key(config: RecommendConfig) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(asdict(config).items()))


class RecommendationObjectiveScope:
    """Discovery-only temporal relevance evidence for one evolution call.

    The proxy objective remains unchanged. When enough strict-temporal relevance
    slices are available, candidate search receives one additive adjustment equal
    to the mean of discovery NDCG and MRR deltas versus the current configuration.
    The reference configuration therefore has an adjustment of exactly zero.

    Optimization requires stronger evidence than a promotion regression check:
    sparse relevance can still guard a selected candidate, but it must not steer a
    multi-generation search until discovery has at least twice the minimum domain
    evidence used by the base recommendation evaluator.
    """

    def __init__(
        self,
        *,
        engine: RecommendationEngine,
        discovery_users: list[str],
        prepared: PreparedRecommendRelevance | None,
        reference: dict[str, Any] | None,
    ) -> None:
        self.engine: Any = engine
        self.discovery_users = list(discovery_users)
        self.prepared = prepared
        self.reference = reference or {}
        self._cache: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
        model = self.reference.get("model") or {}
        self.reference_ndcg = float(model.get("ndcg", 0.0))
        self.reference_mrr = float(model.get("mrr", 0.0))
        self.samples = int(self.reference.get("users", 0) or 0)
        self.measurable = bool(self.reference.get("available"))
        self.available = bool(
            self.measurable
            and self.samples >= MIN_RELEVANCE_OBJECTIVE_EVIDENCE
        )

    def delta_for(self, config: RecommendConfig) -> float:
        if not self.available or self.prepared is None:
            return 0.0
        key = _config_key(config)
        evidence = self._cache.get(key)
        if evidence is None:
            evidence = self.prepared.evaluate(config)
            self._cache[key] = evidence
        if not evidence.get("available"):
            return 0.0
        model = evidence.get("model") or {}
        ndcg_delta = float(model.get("ndcg", 0.0)) - self.reference_ndcg
        mrr_delta = float(model.get("mrr", 0.0)) - self.reference_mrr
        return 0.5 * (ndcg_delta + mrr_delta)

    def annotate(self, result: dict[str, Any]) -> dict[str, Any]:
        annotated = dict(result)
        evolution = dict(annotated.get("evolution") or {})
        evidence: dict[str, Any] = {
            "available": self.available,
            "scope": "discovery_users_only",
            "protocol": self.reference.get("protocol"),
            "temporal_scope": self.reference.get("temporal_scope"),
            "point_in_time_item_features": self.reference.get("point_in_time_item_features"),
            "samples": self.samples,
            "minimum_samples": MIN_RELEVANCE_OBJECTIVE_EVIDENCE,
            "aggregation": "mean_delta_ndcg_mrr",
        }
        if not self.available:
            evidence["reason"] = (
                "insufficient_discovery_evidence"
                if self.measurable
                else "temporal_relevance_unavailable"
            )
            evolution["relevance_objective"] = evidence
            annotated["evolution"] = evolution
            return annotated

        selected_delta = 0.0
        candidate_raw = result.get("candidate_config")
        if isinstance(candidate_raw, dict):
            try:
                selected_delta = self.delta_for(RecommendConfig(**candidate_raw))
            except (TypeError, ValueError):
                selected_delta = 0.0
        evidence.update(
            {
                "reference_ndcg": self.reference_ndcg,
                "reference_mrr": self.reference_mrr,
                "selected_adjustment": round(selected_delta, 6),
                "evaluated_configs": len(self._cache),
            }
        )
        evolution["relevance_objective"] = evidence
        annotated["evolution"] = evolution
        return annotated


class _ConfigTrackingRecommendationEngine:
    """Delegate serving while exposing the config currently being evaluated."""

    def __init__(self, inner: RecommendationEngine) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def with_config(self, config: RecommendConfig) -> RecommendationEngine:
        _ACTIVE_CONFIG.set(config)
        return self._inner.with_config(config)


def _relevance_aware_recommend_objective(
    report: dict[str, Any],
    robust: dict[str, float] | None = None,
) -> float:
    base = _ORIGINAL_RECOMMEND_OBJECTIVE(report, robust)
    scope = _ACTIVE_SCOPE.get()
    config = _ACTIVE_CONFIG.get()
    if scope is None or config is None or not scope.available:
        return base
    return base + scope.delta_for(config)


def install_recommend_objective_router() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    core._recommend_objective = _relevance_aware_recommend_objective
    _INSTALLED = True


@contextmanager
def recommend_relevance_objective(
    catalog: Catalog,
    current: RecommendationEngine,
) -> Iterator[RecommendationObjectiveScope]:
    """Route one evolution call through discovery-only temporal relevance evidence."""

    users = core._stable_limit(current.known_users(), lambda user: user)
    discovery_users, _ = core._stable_split(users, lambda user: user)
    prepared = prepare_recommend_relevance(
        catalog,
        current,
        users_override=discovery_users,
        k=10,
    )
    reference = prepared.evaluate(current.config)
    scope = RecommendationObjectiveScope(
        engine=current,
        discovery_users=discovery_users,
        prepared=prepared,
        reference=reference,
    )
    if not scope.available:
        yield scope
        return

    scope.engine = _ConfigTrackingRecommendationEngine(current)
    scope_token = _ACTIVE_SCOPE.set(scope)
    config_token = _ACTIVE_CONFIG.set(current.config)
    try:
        yield scope
    finally:
        _ACTIVE_CONFIG.reset(config_token)
        _ACTIVE_SCOPE.reset(scope_token)


__all__ = [
    "MIN_RELEVANCE_OBJECTIVE_EVIDENCE",
    "RecommendationObjectiveScope",
    "install_recommend_objective_router",
    "recommend_relevance_objective",
]
