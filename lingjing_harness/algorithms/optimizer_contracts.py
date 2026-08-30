"""Typed proposal-time evidence contracts for external optimizers.

An optimizer may use already-computed discovery evidence to avoid proposing
obviously poor regions, but it must not infer product safety thresholds from generic
field names or duplicate promotion authority. The public evolution boundary sets an
explicit surface scope. The evaluator's owning module then distinguishes proxy from
production search because those routes intentionally have different discovery-time
robustness tolerances.

These constraints are proposal filters only. Downstream independent holdout, trust,
activation, and rollback remain authoritative and are always recomputed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from math import isfinite
from typing import Any, Callable, Iterator


_OPTIMIZER_SURFACE: ContextVar[str] = ContextVar("xushu_optimizer_surface", default="")


@dataclass(frozen=True, slots=True)
class OptimizerOutcomeConstraint:
    """One modeled outcome constraint represented as value <= 0 when feasible."""

    metric: str
    relation: str
    threshold: float

    def __post_init__(self) -> None:
        if not str(self.metric).strip():
            raise ValueError("optimizer constraint metric must not be empty")
        if self.relation not in {"upper", "lower"}:
            raise ValueError("optimizer constraint relation must be upper or lower")
        if not isfinite(float(self.threshold)):
            raise ValueError("optimizer constraint threshold must be finite")

    def violation(self, robustness: dict[str, Any]) -> float:
        """Return a signed constraint value where <= 0 means feasible."""

        if self.metric not in robustness:
            raise ValueError(f"optimizer evidence is missing robustness metric: {self.metric}")
        try:
            observed = float(robustness[self.metric])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"optimizer robustness metric must be numeric: {self.metric}"
            ) from exc
        if not isfinite(observed):
            raise ValueError(f"optimizer robustness metric must be finite: {self.metric}")
        if self.relation == "upper":
            return observed - float(self.threshold)
        return float(self.threshold) - observed

    def dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "relation": self.relation,
            "threshold": float(self.threshold),
            "feasible_when": "modeled_value<=0",
        }


@dataclass(frozen=True, slots=True)
class OptimizerEvidenceContract:
    """Discovery evidence an optimizer is allowed to consume for proposal search."""

    surface: str
    evidence_route: str
    objective_names: tuple[str, str]
    constraints: tuple[OptimizerOutcomeConstraint, ...]

    def __post_init__(self) -> None:
        if self.surface not in {"search", "recommend"}:
            raise ValueError("optimizer evidence surface must be search or recommend")
        if self.evidence_route not in {"proxy", "production"}:
            raise ValueError("optimizer evidence route must be proxy or production")
        if len(self.objective_names) != 2 or any(
            not str(name).strip() for name in self.objective_names
        ):
            raise ValueError("optimizer evidence contract requires exactly two objectives")
        if not self.constraints:
            raise ValueError("optimizer evidence contract requires outcome constraints")

    def outcome_values(self, row: dict[str, Any]) -> tuple[float, ...]:
        report = row.get("report") if isinstance(row.get("report"), dict) else {}
        robust = row.get("robustness") if isinstance(row.get("robustness"), dict) else {}
        try:
            primary = float(row["objective"])
            domain_quality = float(
                report.get(
                    "quality",
                    report.get(
                        "business_reward",
                        report.get("recall", report.get("coverage", 0.0)),
                    ),
                )
                or 0.0
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("optimizer row is missing finite objective evidence") from exc
        values = [primary, domain_quality]
        values.extend(constraint.violation(robust) for constraint in self.constraints)
        if not all(isfinite(value) for value in values):
            raise ValueError("optimizer outcome values must be finite")
        return tuple(values)

    def dict(self) -> dict[str, Any]:
        return {
            "surface": self.surface,
            "evidence_route": self.evidence_route,
            "objectives": list(self.objective_names),
            "constraints": [constraint.dict() for constraint in self.constraints],
            "authority": "proposal_search_only",
        }


def _contract(
    surface: str,
    route: str,
    *,
    max_worse_share: float,
    min_worst_delta: float,
) -> OptimizerEvidenceContract:
    return OptimizerEvidenceContract(
        surface=surface,
        evidence_route=route,
        objective_names=("primary_objective", "domain_quality"),
        constraints=(
            OptimizerOutcomeConstraint("worse_share", "upper", max_worse_share),
            OptimizerOutcomeConstraint("worst_delta", "lower", min_worst_delta),
        ),
    )


# These are discovery-time proposal contracts mirroring the currently authoritative
# downstream safety routes. Production search intentionally permits a wider discovery
# region because business reward + independent temporal holdout are applied later.
_SEARCH_PROXY = _contract(
    "search", "proxy", max_worse_share=0.34, min_worst_delta=-0.35
)
_SEARCH_PRODUCTION = _contract(
    "search", "production", max_worse_share=0.40, min_worst_delta=-0.40
)
_RECOMMEND_PROXY = _contract(
    "recommend", "proxy", max_worse_share=0.40, min_worst_delta=-0.30
)
_RECOMMEND_PRODUCTION = _contract(
    "recommend", "production", max_worse_share=0.40, min_worst_delta=-0.30
)


@contextmanager
def optimizer_evidence_scope(surface: str) -> Iterator[str]:
    surface = str(surface or "").strip().lower()
    if surface not in {"search", "recommend"}:
        raise ValueError("optimizer evidence surface must be search or recommend")
    token = _OPTIMIZER_SURFACE.set(surface)
    try:
        yield surface
    finally:
        _OPTIMIZER_SURFACE.reset(token)


def current_optimizer_surface() -> str:
    return _OPTIMIZER_SURFACE.get()


def attach_optimizer_evidence_contract(
    evaluate: Callable[..., Any],
    contract: OptimizerEvidenceContract,
) -> Callable[..., Any]:
    """Allow an evaluator to override the standard route contract explicitly."""

    setattr(evaluate, "_optimizer_evidence_contract", contract)
    return evaluate


def optimizer_evidence_contract(evaluate: Callable[..., Any]) -> OptimizerEvidenceContract:
    """Resolve a fail-closed proposal contract from explicit surface + route ownership."""

    explicit = getattr(evaluate, "_optimizer_evidence_contract", None)
    if isinstance(explicit, OptimizerEvidenceContract):
        return explicit

    surface = current_optimizer_surface()
    if surface not in {"search", "recommend"}:
        raise RuntimeError(
            "constrained optimizer requires an explicit optimizer evidence surface"
        )
    module = str(getattr(evaluate, "__module__", ""))
    route = "production" if module.endswith("production_evolution") else "proxy"
    if surface == "search":
        return _SEARCH_PRODUCTION if route == "production" else _SEARCH_PROXY
    return _RECOMMEND_PRODUCTION if route == "production" else _RECOMMEND_PROXY


__all__ = [
    "OptimizerOutcomeConstraint",
    "OptimizerEvidenceContract",
    "optimizer_evidence_scope",
    "current_optimizer_surface",
    "attach_optimizer_evidence_contract",
    "optimizer_evidence_contract",
]
