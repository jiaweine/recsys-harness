from __future__ import annotations

from typing import Any, Iterable

from . import evolution_core as core


# Durable credit should steer exploration without permanently banning an arm.
# The cap limits historical inertia; fresh response-surface evidence still has a
# first-class role on every evolution run.
MAX_EFFECTIVE_CREDIT_MASS = 12.0
REPEATED_FAILURE_MARGIN = 2.0


_ORIGINAL_HISTORY_POSTERIORS = core._history_posteriors
_INSTALLED = False


def _iter_credit_rows(remembered: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for row in remembered:
        if not isinstance(row, dict) or str(row.get("status") or "") != "credit":
            continue
        credit = row.get("credit")
        if isinstance(credit, dict):
            yield credit


def _credit_aware_history_posteriors(
    base: dict[str, Any],
    remembered: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    group_totals: dict[str, float] | None = None,
) -> dict[str, tuple[float, float]]:
    posterior = dict(
        _ORIGINAL_HISTORY_POSTERIORS(
            base,
            remembered,
            dimensions,
            group_totals,
        )
    )
    # Global response-surface routing only consumes global-domain credit. Segment
    # credit is read by the segment portfolio filter below so one pathology does
    # not poison an otherwise useful arm for all traffic.
    for credit in _iter_credit_rows(remembered):
        domain = str(credit.get("domain") or "")
        if ".segment." in domain:
            continue
        arm = str(credit.get("arm") or "")
        if arm not in posterior:
            continue
        try:
            positive = max(0.0, min(MAX_EFFECTIVE_CREDIT_MASS, float(credit.get("positive", 0.0))))
            negative = max(0.0, min(MAX_EFFECTIVE_CREDIT_MASS, float(credit.get("negative", 0.0))))
        except (TypeError, ValueError):
            continue
        alpha, beta = posterior[arm]
        posterior[arm] = (alpha + positive, beta + negative)
    return posterior


def install_credit_router() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    core._history_posteriors = _credit_aware_history_posteriors
    _INSTALLED = True


def credit_snapshot(
    remembered: Iterable[dict[str, Any]],
    *,
    domain: str,
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for credit in _iter_credit_rows(remembered):
        if str(credit.get("domain") or "") != domain:
            continue
        arm = str(credit.get("arm") or "")
        if not arm:
            continue
        rows[arm] = dict(credit)
    return rows


def filter_segment_candidates(
    *,
    base_config: dict[str, Any],
    candidates: list[dict[str, Any]],
    dimensions: list[core.EvolutionDimension],
    remembered: Iterable[dict[str, Any]],
    domain: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Avoid exact local retries after repeated independent segment failures.

    A single failure remains exploratory evidence, not a ban. Once an arm has at
    least two more validated failures than successes in this exact segment, a
    candidate composed entirely of such arms is skipped. Mixed candidates remain
    eligible, so the system can still discover interactions that rescue a weak
    standalone mutation. The global/base candidate is never removed.
    """

    snapshot = credit_snapshot(remembered, domain=domain)
    blocked = {
        arm
        for arm, row in snapshot.items()
        if float(row.get("negative", 0.0)) - float(row.get("positive", 0.0))
        >= REPEATED_FAILURE_MARGIN
    }
    if not blocked:
        return list(candidates), {
            "domain": domain,
            "blocked_arms": [],
            "pruned_candidates": 0,
        }

    kept: list[dict[str, Any]] = []
    pruned = 0
    for candidate in candidates:
        try:
            signature = core._config_signature(base_config, candidate, dimensions)
        except (TypeError, ValueError, KeyError):
            kept.append(candidate)
            continue
        mutated = [arm for arm in signature if arm != "local:neutral"]
        if mutated and all(arm in blocked for arm in mutated):
            pruned += 1
            continue
        kept.append(candidate)

    if not kept and candidates:
        kept = [dict(candidates[0])]
    return kept, {
        "domain": domain,
        "blocked_arms": sorted(blocked),
        "pruned_candidates": pruned,
    }


__all__ = [
    "MAX_EFFECTIVE_CREDIT_MASS",
    "REPEATED_FAILURE_MARGIN",
    "install_credit_router",
    "credit_snapshot",
    "filter_segment_candidates",
]
