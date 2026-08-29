"""Explicit experiment contracts built on counterfactual evidence.

An OPE report can support the decision to run a controlled online experiment; it
never grants production activation authority by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

from .counterfactual import CounterfactualRecord, evaluate_off_policy
from .counterfactual_robust import evaluate_robust_off_policy


_BASE_ESTIMATORS = frozenset({"ips", "snips", "dr"})
_ROBUST_ESTIMATORS = frozenset({"raw_dr", "switch_dr", "dros"})
_SUPPORTED_ESTIMATORS = _BASE_ESTIMATORS | _ROBUST_ESTIMATORS


@dataclass(frozen=True, slots=True)
class ExperimentCriteria:
    """Product-owned evidence thresholds for advancing to an online test.

    The harness intentionally does not hide these thresholds inside optimizer
    recipes. Teams define the amount of evidence and overlap they consider enough
    for their product/risk class. Effective-sample-ratio eligibility is evaluated
    on raw importance weights before clipping so variance reduction cannot make
    weak logging-policy overlap look stronger than it actually is.

    ``maximum_estimator_spread`` is optional and disabled by default for backward
    compatibility. When supplied, the experiment also requires Raw-DR, Switch-DR
    and DRos to agree within the declared value-scale tolerance. This turns strong
    estimator disagreement into an explicit evidence blocker rather than silently
    choosing the most optimistic estimate.
    """

    minimum_samples: int
    minimum_effective_sample_ratio: float
    maximum_clipped_share: float
    minimum_support_coverage: float
    minimum_probability_positive: float
    minimum_estimated_delta: float = 0.0
    maximum_estimator_spread: float | None = None

    def __post_init__(self) -> None:
        if int(self.minimum_samples) < 1:
            raise ValueError("minimum_samples must be >= 1")
        for name, value in (
            ("minimum_effective_sample_ratio", self.minimum_effective_sample_ratio),
            ("maximum_clipped_share", self.maximum_clipped_share),
            ("minimum_support_coverage", self.minimum_support_coverage),
            ("minimum_probability_positive", self.minimum_probability_positive),
        ):
            number = float(value)
            if not isfinite(number) or number < 0.0 or number > 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if not isfinite(float(self.minimum_estimated_delta)):
            raise ValueError("minimum_estimated_delta must be finite")
        if self.maximum_estimator_spread is not None:
            spread = float(self.maximum_estimator_spread)
            if not isfinite(spread) or spread < 0.0:
                raise ValueError("maximum_estimator_spread must be finite and >= 0")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExperimentCriteria":
        if not isinstance(raw, Mapping):
            raise ValueError("experiment criteria must be an object")
        required = (
            "minimum_samples",
            "minimum_effective_sample_ratio",
            "maximum_clipped_share",
            "minimum_support_coverage",
            "minimum_probability_positive",
        )
        missing = [name for name in required if raw.get(name) in (None, "")]
        if missing:
            raise ValueError(
                "experiment criteria are missing explicit thresholds: " + ", ".join(missing)
            )
        try:
            spread_raw = raw.get("maximum_estimator_spread")
            return cls(
                minimum_samples=int(raw["minimum_samples"]),
                minimum_effective_sample_ratio=float(raw["minimum_effective_sample_ratio"]),
                maximum_clipped_share=float(raw["maximum_clipped_share"]),
                minimum_support_coverage=float(raw["minimum_support_coverage"]),
                minimum_probability_positive=float(raw["minimum_probability_positive"]),
                minimum_estimated_delta=float(raw.get("minimum_estimated_delta", 0.0)),
                maximum_estimator_spread=(
                    None
                    if spread_raw in (None, "")
                    else float(spread_raw)
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "must" in str(exc):
                raise
            raise ValueError("experiment criteria contain invalid numeric values") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_samples": self.minimum_samples,
            "minimum_effective_sample_ratio": self.minimum_effective_sample_ratio,
            "maximum_clipped_share": self.maximum_clipped_share,
            "minimum_support_coverage": self.minimum_support_coverage,
            "minimum_probability_positive": self.minimum_probability_positive,
            "minimum_estimated_delta": self.minimum_estimated_delta,
            "maximum_estimator_spread": self.maximum_estimator_spread,
        }


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    experiment_id: str
    surface: str
    hypothesis: str
    logging_policy_id: str
    candidate_policy_id: str
    primary_estimator: str
    criteria: ExperimentCriteria
    importance_weight_cap: float = 20.0

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id must not be empty")
        if self.surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        if not self.hypothesis.strip():
            raise ValueError("experiment hypothesis must not be empty")
        if not self.logging_policy_id.strip() or not self.candidate_policy_id.strip():
            raise ValueError("logging_policy_id and candidate_policy_id are required")
        if self.primary_estimator not in _SUPPORTED_ESTIMATORS:
            raise ValueError(
                "primary_estimator must be one of "
                + ", ".join(sorted(_SUPPORTED_ESTIMATORS))
            )
        cap = float(self.importance_weight_cap)
        if not isfinite(cap) or cap < 1.0 or cap > 1000.0:
            raise ValueError("importance_weight_cap must be within [1, 1000]")

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ExperimentSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("experiment spec must be an object")
        criteria = ExperimentCriteria.from_dict(raw.get("criteria") or {})
        return cls(
            experiment_id=str(raw.get("experiment_id") or "").strip(),
            surface=str(raw.get("surface") or "").strip().lower(),
            hypothesis=str(raw.get("hypothesis") or "").strip(),
            logging_policy_id=str(raw.get("logging_policy_id") or "").strip(),
            candidate_policy_id=str(raw.get("candidate_policy_id") or "").strip(),
            primary_estimator=str(raw.get("primary_estimator") or "").strip().lower(),
            criteria=criteria,
            importance_weight_cap=float(raw.get("importance_weight_cap", 20.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "surface": self.surface,
            "hypothesis": self.hypothesis,
            "logging_policy_id": self.logging_policy_id,
            "candidate_policy_id": self.candidate_policy_id,
            "primary_estimator": self.primary_estimator,
            "criteria": self.criteria.to_dict(),
            "importance_weight_cap": self.importance_weight_cap,
        }


def evaluate_counterfactual_experiment(
    records: Iterable[CounterfactualRecord],
    spec: ExperimentSpec,
    *,
    bootstrap_iterations: int = 600,
) -> dict[str, Any]:
    """Assess whether explicit OPE evidence is sufficient for an online test.

    Passing this gate means only "eligible for a controlled online experiment".
    It never means "safe to deploy globally" and never activates a policy.
    Robust DR estimators are evaluated in parallel when the required reward model
    is complete. Their tuning diagnostics remain visible and cannot bypass the raw
    overlap/support gates owned by this experiment contract.
    """

    rows = list(records)
    for row in rows:
        if row.surface != spec.surface:
            raise ValueError("counterfactual record surface does not match experiment")
        if row.logging_policy_id != spec.logging_policy_id:
            raise ValueError("counterfactual record logging_policy_id does not match experiment")
        if row.target_policy_id != spec.candidate_policy_id:
            raise ValueError("counterfactual record target_policy_id does not match experiment")

    report = evaluate_off_policy(
        rows,
        importance_weight_cap=spec.importance_weight_cap,
        bootstrap_iterations=bootstrap_iterations,
    )
    robust_report = evaluate_robust_off_policy(
        rows,
        bootstrap_iterations=bootstrap_iterations,
    )
    criteria = spec.criteria
    diagnostics = report.get("diagnostics") or {}

    if spec.primary_estimator in _ROBUST_ESTIMATORS:
        primary_estimators = robust_report.get("estimators") or {}
        primary_confidences = robust_report.get("confidence") or {}
    else:
        primary_estimators = report.get("estimators") or {}
        primary_confidences = report.get("confidence") or {}

    primary = primary_estimators.get(spec.primary_estimator) or {
        "available": False,
        "value": None,
        "delta_vs_logged": None,
    }
    primary_confidence = primary_confidences.get(spec.primary_estimator) or {
        "available": False,
        "probability_positive": None,
    }

    blockers: list[str] = []
    samples = int(report.get("samples", 0) or 0)
    if samples < criteria.minimum_samples:
        blockers.append(f"samples<{criteria.minimum_samples}")

    # Clipping is a variance-control choice, not evidence that the logging policy
    # had good overlap with the target policy. Gate on raw-weight ESS so a large
    # cap intervention cannot manufacture an acceptable effective sample ratio.
    raw_ess_ratio = float(
        diagnostics.get(
            "raw_effective_sample_ratio",
            diagnostics.get("effective_sample_ratio", 0.0),
        )
        or 0.0
    )
    if raw_ess_ratio < criteria.minimum_effective_sample_ratio:
        blockers.append(
            f"effective_sample_ratio<{criteria.minimum_effective_sample_ratio:g}"
        )

    clipped_share = float(diagnostics.get("clipped_share", 0.0) or 0.0)
    if clipped_share > criteria.maximum_clipped_share:
        blockers.append(f"clipped_share>{criteria.maximum_clipped_share:g}")
    support = float(
        diagnostics.get("logged_action_support_coverage", 0.0) or 0.0
    )
    if support < criteria.minimum_support_coverage:
        blockers.append(
            f"support_coverage<{criteria.minimum_support_coverage:g}"
        )
    if not primary.get("available"):
        blockers.append(f"{spec.primary_estimator}_unavailable")
    else:
        delta = float(primary.get("delta_vs_logged", 0.0) or 0.0)
        if delta < criteria.minimum_estimated_delta:
            blockers.append(
                f"estimated_delta<{criteria.minimum_estimated_delta:g}"
            )
    probability = primary_confidence.get("probability_positive")
    if probability is None or float(probability) < criteria.minimum_probability_positive:
        blockers.append(
            f"probability_positive<{criteria.minimum_probability_positive:g}"
        )

    if criteria.maximum_estimator_spread is not None:
        if not robust_report.get("available"):
            blockers.append("robust_estimators_unavailable_for_agreement_gate")
        else:
            robust_diagnostics = robust_report.get("diagnostics") or {}
            spread = float(robust_diagnostics.get("robust_estimator_spread", 0.0) or 0.0)
            if spread > criteria.maximum_estimator_spread:
                blockers.append(
                    f"estimator_spread>{criteria.maximum_estimator_spread:g}"
                )

    blockers = list(dict.fromkeys(blockers))
    eligible = not blockers
    return {
        "experiment": spec.to_dict(),
        "counterfactual_evaluation": report,
        "robust_counterfactual_evaluation": robust_report,
        "decision": {
            "eligible_for_online_test": eligible,
            "automatic_activation": False,
            "primary_estimator": spec.primary_estimator,
            "primary_estimator_family": (
                "robust_doubly_robust"
                if spec.primary_estimator in _ROBUST_ESTIMATORS
                else "base_counterfactual"
            ),
            "effective_sample_ratio_basis": "raw_importance_weights",
            "estimator_agreement_gate": criteria.maximum_estimator_spread is not None,
            "blockers": blockers,
            "next_step": (
                "controlled_online_experiment"
                if eligible
                else "collect_or_improve_counterfactual_evidence"
            ),
        },
    }


__all__ = [
    "ExperimentCriteria",
    "ExperimentSpec",
    "evaluate_counterfactual_experiment",
]
