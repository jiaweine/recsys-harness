"""Controlled-online-experiment gates for explicit slate OPE evidence.

Slate estimators use different causal assumptions and overlap diagnostics than a
single-action contextual bandit. This module therefore keeps a separate product
contract rather than pretending single-action clipping/support fields are valid for
ranked lists.

Passing this gate means only that evidence is sufficient to *run a controlled
online experiment*. It never activates a strategy or changes trust thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, Iterable, Mapping

from .slate_counterfactual import SlatePositionRecord, evaluate_slate_off_policy


_SUPPORTED_SLATE_ESTIMATORS = frozenset({"iips", "rips", "cascade_dr"})


def _strict_integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not isfinite(number) or not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def _strict_boolean(value: Any, *, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(f"{name} must be boolean")


@dataclass(frozen=True, slots=True)
class SlateExperimentCriteria:
    """Product-owned thresholds for advancing slate evidence to an online test."""

    minimum_slates: int
    minimum_effective_sample_ratio: float
    maximum_importance_weight: float
    minimum_probability_positive: float
    minimum_estimated_delta: float = 0.0
    require_consistent_slate_length: bool = True
    maximum_estimator_spread: float | None = None

    def __post_init__(self) -> None:
        minimum_slates = _strict_integer(self.minimum_slates, name="minimum_slates")
        if minimum_slates < 1:
            raise ValueError("minimum_slates must be >= 1")
        object.__setattr__(self, "minimum_slates", minimum_slates)

        ratio = float(self.minimum_effective_sample_ratio)
        if not isfinite(ratio) or ratio < 0.0 or ratio > 1.0:
            raise ValueError("minimum_effective_sample_ratio must be within [0, 1]")
        object.__setattr__(self, "minimum_effective_sample_ratio", ratio)

        max_weight = float(self.maximum_importance_weight)
        if not isfinite(max_weight) or max_weight <= 0.0:
            raise ValueError("maximum_importance_weight must be finite and > 0")
        object.__setattr__(self, "maximum_importance_weight", max_weight)

        probability = float(self.minimum_probability_positive)
        if not isfinite(probability) or probability < 0.0 or probability > 1.0:
            raise ValueError("minimum_probability_positive must be within [0, 1]")
        object.__setattr__(self, "minimum_probability_positive", probability)

        delta = float(self.minimum_estimated_delta)
        if not isfinite(delta):
            raise ValueError("minimum_estimated_delta must be finite")
        object.__setattr__(self, "minimum_estimated_delta", delta)

        consistent = _strict_boolean(
            self.require_consistent_slate_length,
            name="require_consistent_slate_length",
        )
        object.__setattr__(self, "require_consistent_slate_length", consistent)

        if self.maximum_estimator_spread is not None:
            spread = float(self.maximum_estimator_spread)
            if not isfinite(spread) or spread < 0.0:
                raise ValueError("maximum_estimator_spread must be finite and >= 0")
            object.__setattr__(self, "maximum_estimator_spread", spread)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SlateExperimentCriteria":
        if not isinstance(raw, Mapping):
            raise ValueError("slate experiment criteria must be an object")
        required = (
            "minimum_slates",
            "minimum_effective_sample_ratio",
            "maximum_importance_weight",
            "minimum_probability_positive",
        )
        missing = [name for name in required if raw.get(name) in (None, "")]
        if missing:
            raise ValueError(
                "slate experiment criteria are missing explicit thresholds: "
                + ", ".join(missing)
            )
        try:
            spread_raw = raw.get("maximum_estimator_spread")
            return cls(
                minimum_slates=_strict_integer(raw["minimum_slates"], name="minimum_slates"),
                minimum_effective_sample_ratio=float(raw["minimum_effective_sample_ratio"]),
                maximum_importance_weight=float(raw["maximum_importance_weight"]),
                minimum_probability_positive=float(raw["minimum_probability_positive"]),
                minimum_estimated_delta=float(raw.get("minimum_estimated_delta", 0.0)),
                require_consistent_slate_length=_strict_boolean(
                    raw.get("require_consistent_slate_length", True),
                    name="require_consistent_slate_length",
                ),
                maximum_estimator_spread=(
                    None if spread_raw in (None, "") else float(spread_raw)
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, ValueError) and "must" in str(exc):
                raise
            raise ValueError("slate experiment criteria contain invalid values") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "minimum_slates": self.minimum_slates,
            "minimum_effective_sample_ratio": self.minimum_effective_sample_ratio,
            "maximum_importance_weight": self.maximum_importance_weight,
            "minimum_probability_positive": self.minimum_probability_positive,
            "minimum_estimated_delta": self.minimum_estimated_delta,
            "require_consistent_slate_length": self.require_consistent_slate_length,
            "maximum_estimator_spread": self.maximum_estimator_spread,
        }


@dataclass(frozen=True, slots=True)
class SlateExperimentSpec:
    experiment_id: str
    surface: str
    hypothesis: str
    logging_policy_id: str
    candidate_policy_id: str
    primary_estimator: str
    criteria: SlateExperimentCriteria

    def __post_init__(self) -> None:
        if not str(self.experiment_id).strip():
            raise ValueError("experiment_id must not be empty")
        if self.surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        if not str(self.hypothesis).strip():
            raise ValueError("experiment hypothesis must not be empty")
        if not str(self.logging_policy_id).strip() or not str(self.candidate_policy_id).strip():
            raise ValueError("logging_policy_id and candidate_policy_id are required")
        if self.primary_estimator not in _SUPPORTED_SLATE_ESTIMATORS:
            raise ValueError(
                "primary_estimator must be one of "
                + ", ".join(sorted(_SUPPORTED_SLATE_ESTIMATORS))
            )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "SlateExperimentSpec":
        if not isinstance(raw, Mapping):
            raise ValueError("slate experiment spec must be an object")
        return cls(
            experiment_id=str(raw.get("experiment_id") or "").strip(),
            surface=str(raw.get("surface") or "").strip().lower(),
            hypothesis=str(raw.get("hypothesis") or "").strip(),
            logging_policy_id=str(raw.get("logging_policy_id") or "").strip(),
            candidate_policy_id=str(raw.get("candidate_policy_id") or "").strip(),
            primary_estimator=str(raw.get("primary_estimator") or "").strip().lower(),
            criteria=SlateExperimentCriteria.from_dict(raw.get("criteria") or {}),
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
        }


def _overlap_basis(estimator: str, diagnostics: Mapping[str, Any]) -> tuple[float, float, str]:
    if estimator == "iips":
        return (
            float(diagnostics.get("minimum_item_position_effective_sample_ratio", 0.0) or 0.0),
            float(diagnostics.get("item_position_weight_max", 0.0) or 0.0),
            "minimum_item_position_effective_sample_ratio",
        )
    position_ess = float(
        diagnostics.get("minimum_cascade_effective_sample_ratio", 0.0) or 0.0
    )
    final_ess = float(diagnostics.get("final_prefix_effective_sample_ratio", 0.0) or 0.0)
    return (
        min(position_ess, final_ess),
        float(diagnostics.get("cascade_weight_max", 0.0) or 0.0),
        "minimum_of_position_and_final_prefix_cascade_effective_sample_ratio",
    )


def evaluate_slate_experiment(
    records: Iterable[SlatePositionRecord],
    spec: SlateExperimentSpec,
    *,
    bootstrap_iterations: int = 600,
) -> dict[str, Any]:
    """Assess whether slate OPE evidence is sufficient for a controlled online test."""

    rows = list(records)
    for row in rows:
        if row.surface != spec.surface:
            raise ValueError("slate record surface does not match experiment")
        if row.logging_policy_id != spec.logging_policy_id:
            raise ValueError("slate record logging_policy_id does not match experiment")
        if row.target_policy_id != spec.candidate_policy_id:
            raise ValueError("slate record target_policy_id does not match experiment")

    report = evaluate_slate_off_policy(rows, bootstrap_iterations=bootstrap_iterations)
    criteria = spec.criteria
    estimators = report.get("estimators") or {}
    confidence = report.get("confidence") or {}
    diagnostics = report.get("diagnostics") or {}
    primary = estimators.get(spec.primary_estimator) or {
        "available": False,
        "value": None,
        "delta_vs_logged": None,
    }
    primary_confidence = confidence.get(spec.primary_estimator) or {
        "available": False,
        "probability_positive": None,
    }
    ess_ratio, weight_max, ess_basis = _overlap_basis(spec.primary_estimator, diagnostics)

    blockers: list[str] = []
    slates = int(report.get("slates", 0) or 0)
    if slates < criteria.minimum_slates:
        blockers.append(f"slates<{criteria.minimum_slates}")
    if criteria.require_consistent_slate_length and len(report.get("slate_lengths") or []) > 1:
        blockers.append("inconsistent_slate_length")
    if ess_ratio < criteria.minimum_effective_sample_ratio:
        blockers.append(
            f"effective_sample_ratio<{criteria.minimum_effective_sample_ratio:g}"
        )
    if weight_max > criteria.maximum_importance_weight:
        blockers.append(f"importance_weight>{criteria.maximum_importance_weight:g}")
    if not primary.get("available"):
        blockers.append(f"{spec.primary_estimator}_unavailable")
    else:
        delta = float(primary.get("delta_vs_logged", 0.0) or 0.0)
        if delta < criteria.minimum_estimated_delta:
            blockers.append(f"estimated_delta<{criteria.minimum_estimated_delta:g}")
    probability = primary_confidence.get("probability_positive")
    if probability is None or float(probability) < criteria.minimum_probability_positive:
        blockers.append(
            f"probability_positive<{criteria.minimum_probability_positive:g}"
        )

    if criteria.maximum_estimator_spread is not None:
        cascade = estimators.get("cascade_dr") or {}
        if not cascade.get("available"):
            blockers.append("cascade_dr_unavailable_for_agreement_gate")
        else:
            spread = float(diagnostics.get("estimator_spread", 0.0) or 0.0)
            if spread > criteria.maximum_estimator_spread:
                blockers.append(
                    f"estimator_spread>{criteria.maximum_estimator_spread:g}"
                )

    blockers = list(dict.fromkeys(blockers))
    eligible = not blockers
    return {
        "experiment": spec.to_dict(),
        "slate_counterfactual_evaluation": report,
        "decision": {
            "eligible_for_online_test": eligible,
            "automatic_activation": False,
            "primary_estimator": spec.primary_estimator,
            "primary_estimator_family": "slate_counterfactual",
            "effective_sample_ratio": round(ess_ratio, 6),
            "effective_sample_ratio_basis": ess_basis,
            "maximum_observed_importance_weight": round(weight_max, 6),
            "estimator_agreement_gate": criteria.maximum_estimator_spread is not None,
            "blockers": blockers,
            "next_step": (
                "controlled_online_experiment"
                if eligible
                else "collect_or_improve_slate_counterfactual_evidence"
            ),
        },
    }


__all__ = [
    "SlateExperimentCriteria",
    "SlateExperimentSpec",
    "evaluate_slate_experiment",
]
