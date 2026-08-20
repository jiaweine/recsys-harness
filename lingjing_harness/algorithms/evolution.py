"""Stable public surface for vertical evolution.

The structural/search machinery remains in ``evolution_core``. Public evolution
routes through ``production_evolution`` so a project-provided RewardSpec and
production exposure log become the primary objective when available, while the
legacy proxy path remains available for local/demo datasets.
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


def evolve_search(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _trust_evidence_gate(_evolve_search(*args, **kwargs))


def evolve_recommend(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _trust_evidence_gate(_evolve_recommend(*args, **kwargs))


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
