"""Stable public surface for vertical evolution.

The structural/search machinery remains in ``evolution_core``. Public evolution
routes through ``production_evolution`` so a project-provided RewardSpec and
production exposure log become the primary objective when available, while the
proxy path remains available for local/demo datasets. Durable positive and
negative arm credit then steers the response-surface prior, and production-aware
runs derive holdout-validated request-segment portfolios around the globally
discovered strategy basin. Sparse segments keep the global strategy as fallback.
"""

from typing import Any

from . import evolution_core as _core
from .credit_routing import install_credit_router
from .optimizer_backends import (
    annotate_optimizer_backend,
    current_optimizer_backend,
    optimizer_backend as select_optimizer_backend,
)
from .production_evolution import (
    evolve_recommend as _evolve_recommend,
    evolve_search as _evolve_search,
)
from .recommend import RecommendConfig
from .recommend_validation import prepare_recommend_relevance
from .segment_credit import attach_recommend_portfolio, attach_search_portfolio


# Install once at the stable public boundary. ``evolution_core._response_surface``
# resolves this module-global function at call time, so replacing it here makes
# every public evolution path credit-aware without duplicating the core search
# machinery or introducing per-run global mutable context.
install_credit_router()

EvolutionDimension = _core.EvolutionDimension
_evolution_schema = _core._evolution_schema
_history_posteriors = _core._history_posteriors
_perturb = _core._perturb
_project = _core._project
_recommend_gates = _core._recommend_gates
_stable_split = _core._stable_split


def _trust_evidence_gate(result: dict[str, Any]) -> dict[str, Any]:
    """Do not call a one-request future slice statistically trustworthy.

    Business replay may still route exploration with sparse production logs, but
    durable trust requires at least two paired future requests and an independent
    domain guardrail holdout. This keeps a tiny log from silently becoming an
    activation certificate.
    """

    business = result.get("business_validation") or {}
    if not business.get("available"):
        return result
    confidence = business.get("confidence") or {}
    independent_guardrail = bool((result.get("validation") or {}).get("holdout", {}).get("independent"))
    samples = int(confidence.get("samples", 0) or 0)
    if result.get("trusted") and (samples < 2 or not independent_guardrail):
        result = dict(result)
        result["trusted"] = False
        result["business_trusted"] = False
        result["trust_blocked_by"] = [
            *(["business_holdout_samples<2"] if samples < 2 else []),
            *([] if independent_guardrail else ["domain_guardrail_holdout_unavailable"]),
        ]
    return result


def _recommend_relevance_gate(
    catalog: Any,
    current: Any,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach cached interaction-temporal relevance evidence to promotion.

    Business reward remains the primary production objective. This gate prevents
    a candidate from being promoted when its warm recommendation relevance
    materially regresses on the same point-in-time interaction slices. Item-side
    features are intentionally reported as snapshot features by the prepared
    evaluator, so this is a regression guardrail rather than a claim of full
    historical-feature reconstruction.
    """

    candidate_raw = result.get("candidate_config")
    if not result.get("evaluation_ready") or not isinstance(candidate_raw, dict):
        return result

    prepared = prepare_recommend_relevance(
        catalog,
        current,
        users_override=current.known_users(),
        k=10,
    )
    reference = prepared.evaluate(current.config)
    candidate = prepared.evaluate(RecommendConfig(**candidate_raw))
    samples = min(int(reference.get("users", 0)), int(candidate.get("users", 0)))
    available = bool(reference.get("available") and candidate.get("available") and samples >= 3)
    ndcg_delta = float(candidate.get("model", {}).get("ndcg", 0.0)) - float(
        reference.get("model", {}).get("ndcg", 0.0)
    )
    mrr_delta = float(candidate.get("model", {}).get("mrr", 0.0)) - float(
        reference.get("model", {}).get("mrr", 0.0)
    )
    safe = not available or (ndcg_delta >= -0.01 and mrr_delta >= -0.015)

    gated = dict(result)
    gated["relevance_validation"] = {
        "available": available,
        "samples": samples,
        "protocol": reference.get("protocol"),
        "temporal_scope": reference.get("temporal_scope"),
        "point_in_time_item_features": reference.get("point_in_time_item_features"),
        "minimum_target_weight": reference.get("minimum_target_weight"),
        "prepared_slices": reference.get("prepared_slices", samples),
        "reference_ndcg": float(reference.get("model", {}).get("ndcg", 0.0)),
        "candidate_ndcg": float(candidate.get("model", {}).get("ndcg", 0.0)),
        "ndcg_delta": round(ndcg_delta, 4),
        "reference_mrr": float(reference.get("model", {}).get("mrr", 0.0)),
        "candidate_mrr": float(candidate.get("model", {}).get("mrr", 0.0)),
        "mrr_delta": round(mrr_delta, 4),
        "passed": safe,
    }
    gated["relevance_guardrail_passed"] = safe
    if not safe:
        gated["safe_to_try"] = False
        blockers = list(gated.get("trust_blocked_by") or [])
        if "recommend_relevance_regression" not in blockers:
            blockers.append("recommend_relevance_regression")
        gated["trust_blocked_by"] = blockers
        if gated.get("trusted"):
            gated["trusted"] = False
        if "business_trusted" in gated:
            gated["business_trusted"] = False
    return gated


def _run_search(
    catalog: Any,
    current: Any,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    remembered = kwargs.get("remembered")
    result = _trust_evidence_gate(_evolve_search(catalog, current, *args, **kwargs))
    return attach_search_portfolio(
        catalog,
        current,
        result,
        remembered=remembered if isinstance(remembered, list) else None,
    )


def _run_recommend(
    catalog: Any,
    current: Any,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    remembered = kwargs.get("remembered")
    result = _trust_evidence_gate(_evolve_recommend(catalog, current, *args, **kwargs))
    result = _recommend_relevance_gate(catalog, current, result)
    return attach_recommend_portfolio(
        catalog,
        current,
        result,
        remembered=remembered if isinstance(remembered, list) else None,
    )


def evolve_search(catalog: Any, current: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    backend_request = kwargs.pop("optimizer_backend", None)
    if backend_request is None:
        backend = current_optimizer_backend()
        return annotate_optimizer_backend(_run_search(catalog, current, *args, **kwargs), backend)
    with select_optimizer_backend(str(backend_request)) as backend:
        return annotate_optimizer_backend(_run_search(catalog, current, *args, **kwargs), backend)


def evolve_recommend(catalog: Any, current: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    backend_request = kwargs.pop("optimizer_backend", None)
    if backend_request is None:
        backend = current_optimizer_backend()
        return annotate_optimizer_backend(_run_recommend(catalog, current, *args, **kwargs), backend)
    with select_optimizer_backend(str(backend_request)) as backend:
        return annotate_optimizer_backend(_run_recommend(catalog, current, *args, **kwargs), backend)


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
