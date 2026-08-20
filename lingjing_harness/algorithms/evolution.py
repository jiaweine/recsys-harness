"""Stable public surface for vertical evolution.

The structural/search machinery remains in ``evolution_core``. Public evolution
routes through ``production_evolution`` so a project-provided RewardSpec and
production exposure log become the primary objective when available, while the
proxy path remains available for local/demo datasets. Production-aware runs then
derive holdout-validated request-segment portfolios around the globally discovered
strategy basin; sparse segments keep the global strategy as their fallback.
"""

from typing import Any

from .evolution_core import (
    EvolutionDimension,
    _evolution_schema,
    _history_posteriors,
    _perturb,
    _project,
    _recommend_gates,
    _stable_split,
)
from .production_evolution import (
    evolve_recommend as _evolve_recommend,
    evolve_search as _evolve_search,
)
from .segment_evolution import attach_recommend_portfolio, attach_search_portfolio


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
    result = _trust_evidence_gate(_evolve_search(catalog, current, *args, **kwargs))
    return attach_search_portfolio(catalog, current, result)


def evolve_recommend(catalog: Any, current: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    result = _trust_evidence_gate(_evolve_recommend(catalog, current, *args, **kwargs))
    return attach_recommend_portfolio(catalog, current, result)


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
