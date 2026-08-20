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
from .production_evolution import (
    evolve_recommend as _evolve_recommend,
    evolve_search as _evolve_search,
)
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


def evolve_search(catalog: Any, current: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    remembered = kwargs.get("remembered")
    result = _trust_evidence_gate(_evolve_search(catalog, current, *args, **kwargs))
    return attach_search_portfolio(
        catalog,
        current,
        result,
        remembered=remembered if isinstance(remembered, list) else None,
    )


def evolve_recommend(catalog: Any, current: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    remembered = kwargs.get("remembered")
    result = _trust_evidence_gate(_evolve_recommend(catalog, current, *args, **kwargs))
    return attach_recommend_portfolio(
        catalog,
        current,
        result,
        remembered=remembered if isinstance(remembered, list) else None,
    )


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
