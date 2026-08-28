"""Explicit contextual-bandit off-policy evaluation.

This module intentionally does not infer target-policy probabilities from rank,
score, or an ExposureEvent logging propensity. IPS/SNIPS/DR are only available
when an integrating policy can state the probability that both the logging and
target policies assign to the *same logged action* for each decision context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import blake2b
from math import isfinite
from random import Random
from statistics import mean
from typing import Any, Iterable, Mapping


@dataclass(frozen=True, slots=True)
class CounterfactualRecord:
    """One contextual-bandit decision with one logged action.

    ``logging_propensity`` is mu(a|x), the probability that the historical policy
    selected ``action_id`` in this decision context. ``target_propensity`` is
    pi(a|x), the probability that the candidate policy would select that same
    action. A deterministic target policy is therefore valid only when the
    adapter can truthfully emit 0/1 probabilities for the logged action.

    Doubly Robust evaluation additionally requires:

    - ``logged_reward_estimate`` = q_hat(x, a_logged)
    - ``target_reward_estimate`` = E_{a~pi(.|x)} q_hat(x, a)

    The two estimates are deliberately separate so a single ambiguous "model
    score" cannot be mistaken for a valid DR baseline.
    """

    decision_id: str
    surface: str
    action_id: str
    reward: float
    logging_propensity: float
    target_propensity: float
    logging_policy_id: str
    target_policy_id: str
    logged_reward_estimate: float | None = None
    target_reward_estimate: float | None = None
    segment: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("decision_id must not be empty")
        if self.surface not in {"search", "recommend"}:
            raise ValueError("surface must be search or recommend")
        if not self.action_id.strip():
            raise ValueError("action_id must not be empty")
        if not self.logging_policy_id.strip() or not self.target_policy_id.strip():
            raise ValueError("logging_policy_id and target_policy_id are required")
        for name, value in (
            ("reward", self.reward),
            ("logging_propensity", self.logging_propensity),
            ("target_propensity", self.target_propensity),
        ):
            if not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not 0.0 < float(self.logging_propensity) <= 1.0:
            raise ValueError("logging_propensity must be within (0, 1]")
        if not 0.0 <= float(self.target_propensity) <= 1.0:
            raise ValueError("target_propensity must be within [0, 1]")
        direct = (
            self.logged_reward_estimate,
            self.target_reward_estimate,
        )
        if (direct[0] is None) != (direct[1] is None):
            raise ValueError(
                "DR reward-model inputs must provide both logged_reward_estimate and target_reward_estimate"
            )
        for name, value in (
            ("logged_reward_estimate", self.logged_reward_estimate),
            ("target_reward_estimate", self.target_reward_estimate),
        ):
            if value is not None and not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "CounterfactualRecord":
        if not isinstance(row, Mapping):
            raise ValueError("counterfactual record must be an object")
        required = (
            "decision_id",
            "surface",
            "action_id",
            "reward",
            "logging_propensity",
            "target_propensity",
            "logging_policy_id",
            "target_policy_id",
        )
        missing = [name for name in required if row.get(name) in (None, "")]
        if missing:
            raise ValueError(
                "counterfactual record is missing explicit fields: " + ", ".join(missing)
            )
        try:
            reward = float(row["reward"])
            logging_propensity = float(row["logging_propensity"])
            target_propensity = float(row["target_propensity"])
            logged_estimate = (
                None
                if row.get("logged_reward_estimate") in (None, "")
                else float(row["logged_reward_estimate"])
            )
            target_estimate = (
                None
                if row.get("target_reward_estimate") in (None, "")
                else float(row["target_reward_estimate"])
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("counterfactual numeric fields must be numbers") from exc
        metadata = row.get("metadata") or {}
        if not isinstance(metadata, Mapping):
            raise ValueError("metadata must be an object")
        return cls(
            decision_id=str(row["decision_id"]).strip(),
            surface=str(row["surface"]).strip().lower(),
            action_id=str(row["action_id"]).strip(),
            reward=reward,
            logging_propensity=logging_propensity,
            target_propensity=target_propensity,
            logging_policy_id=str(row["logging_policy_id"]).strip(),
            target_policy_id=str(row["target_policy_id"]).strip(),
            logged_reward_estimate=logged_estimate,
            target_reward_estimate=target_estimate,
            segment=str(row.get("segment") or "").strip(),
            metadata=dict(metadata),
        )

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "decision_id": self.decision_id,
            "surface": self.surface,
            "action_id": self.action_id,
            "reward": self.reward,
            "logging_propensity": self.logging_propensity,
            "target_propensity": self.target_propensity,
            "logging_policy_id": self.logging_policy_id,
            "target_policy_id": self.target_policy_id,
        }
        if self.logged_reward_estimate is not None:
            row["logged_reward_estimate"] = self.logged_reward_estimate
            row["target_reward_estimate"] = self.target_reward_estimate
        if self.segment:
            row["segment"] = self.segment
        if self.metadata:
            row["metadata"] = dict(self.metadata)
        return row


def _validate_weight_cap(weight_cap: float) -> float:
    try:
        value = float(weight_cap)
    except (TypeError, ValueError) as exc:
        raise ValueError("importance_weight_cap must be numeric") from exc
    if not isfinite(value) or value < 1.0 or value > 1000.0:
        raise ValueError("importance_weight_cap must be within [1, 1000]")
    return value


def _ordered_records(records: Iterable[CounterfactualRecord]) -> list[CounterfactualRecord]:
    rows = sorted(list(records), key=lambda row: row.decision_id)
    seen: set[str] = set()
    for row in rows:
        if row.decision_id in seen:
            raise ValueError(
                "counterfactual data must contain exactly one logged action per decision_id"
            )
        seen.add(row.decision_id)

    if rows:
        first = rows[0]
        for row in rows[1:]:
            if row.surface != first.surface:
                raise ValueError("counterfactual report must contain exactly one surface")
            if row.logging_policy_id != first.logging_policy_id:
                raise ValueError(
                    "counterfactual report must contain exactly one logging_policy_id"
                )
            if row.target_policy_id != first.target_policy_id:
                raise ValueError(
                    "counterfactual report must contain exactly one target_policy_id"
                )
    return rows


def _effective_sample_size(weights: list[float]) -> float:
    weight_sum = sum(weights)
    square_sum = sum(weight * weight for weight in weights)
    return (weight_sum * weight_sum / square_sum) if square_sum > 0.0 else 0.0


def _point_estimates(
    rows: list[CounterfactualRecord],
    weight_cap: float,
) -> dict[str, Any]:
    if not rows:
        return {
            "logged_mean": None,
            "ips": None,
            "snips": None,
            "dr": None,
            "raw_weights": [],
            "weights": [],
            "direct_samples": 0,
        }
    raw_weights = [
        float(row.target_propensity) / float(row.logging_propensity)
        for row in rows
    ]
    weights = [min(weight_cap, value) for value in raw_weights]
    logged_mean = mean(float(row.reward) for row in rows)
    ips = mean(weight * float(row.reward) for row, weight in zip(rows, weights))
    weight_sum = sum(weights)
    snips = (
        sum(weight * float(row.reward) for row, weight in zip(rows, weights))
        / weight_sum
        if weight_sum > 0.0
        else None
    )
    direct_rows = [
        row
        for row in rows
        if row.logged_reward_estimate is not None
        and row.target_reward_estimate is not None
    ]
    dr = None
    if len(direct_rows) == len(rows):
        dr = mean(
            float(row.target_reward_estimate)
            + weight
            * (float(row.reward) - float(row.logged_reward_estimate))
            for row, weight in zip(rows, weights)
        )
    return {
        "logged_mean": logged_mean,
        "ips": ips,
        "snips": snips,
        "dr": dr,
        "raw_weights": raw_weights,
        "weights": weights,
        "direct_samples": len(direct_rows),
    }


def _confidence_for_estimator(
    rows: list[CounterfactualRecord],
    *,
    estimator: str,
    weight_cap: float,
    iterations: int,
) -> dict[str, Any]:
    point = _point_estimates(rows, weight_cap)
    value = point.get(estimator)
    baseline = point.get("logged_mean")
    if value is None or baseline is None:
        return {
            "available": False,
            "samples": len(rows),
            "delta": None,
            "ci95": None,
            "probability_positive": None,
        }
    observed = float(value) - float(baseline)
    if len(rows) <= 1:
        return {
            "available": False,
            "samples": len(rows),
            "delta": round(observed, 6),
            "ci95": None,
            "probability_positive": None,
            "reason": "at least two decisions are required for bootstrap uncertainty",
        }

    stable = "|".join(
        (
            f"{row.decision_id}:{row.reward:.10g}:{row.logging_propensity:.10g}:"
            f"{row.target_propensity:.10g}:{row.logged_reward_estimate}:{row.target_reward_estimate}"
        )
        for row in rows
    )
    seed = int.from_bytes(
        blake2b(f"{estimator}|{weight_cap}|{stable}".encode("utf-8"), digest_size=8).digest(),
        "little",
    )
    rng = Random(seed)
    draws: list[float] = []
    count = len(rows)
    for _ in range(max(100, min(10000, int(iterations)))):
        sample = [rows[rng.randrange(count)] for _ in range(count)]
        sampled = _point_estimates(sample, weight_cap)
        sampled_value = sampled.get(estimator)
        sampled_baseline = sampled.get("logged_mean")
        if sampled_value is None or sampled_baseline is None:
            continue
        draws.append(float(sampled_value) - float(sampled_baseline))
    if not draws:
        return {
            "available": False,
            "samples": len(rows),
            "delta": round(observed, 6),
            "ci95": None,
            "probability_positive": None,
        }
    draws.sort()
    low = draws[max(0, int(len(draws) * 0.025) - 1)]
    high = draws[min(len(draws) - 1, int(len(draws) * 0.975))]
    positive = sum(1 for draw in draws if draw > 0.0) / len(draws)
    return {
        "available": True,
        "samples": len(rows),
        "delta": round(observed, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "probability_positive": round(positive, 4),
    }


def evaluate_off_policy(
    records: Iterable[CounterfactualRecord],
    *,
    importance_weight_cap: float = 20.0,
    bootstrap_iterations: int = 600,
) -> dict[str, Any]:
    """Evaluate one target policy with explicit IPS, SNIPS and optional DR.

    The estimators are valid only to the extent that the supplied propensities and
    reward model are valid. Weight clipping is always reported because clipping
    reduces variance by accepting bias. Raw-weight ESS is reported separately
    from clipped-weight ESS so clipping cannot make poor overlap look healthier.
    This function therefore returns diagnostics instead of turning an OPE number
    into an automatic trust decision.
    """

    weight_cap = _validate_weight_cap(importance_weight_cap)
    rows = _ordered_records(records)
    if not rows:
        return {
            "available": False,
            "samples": 0,
            "estimators": {},
            "confidence": {},
            "diagnostics": {
                "importance_weight_cap": weight_cap,
                "effective_sample_size": 0.0,
                "effective_sample_ratio": 0.0,
                "raw_effective_sample_size": 0.0,
                "raw_effective_sample_ratio": 0.0,
                "clipped_effective_sample_size": 0.0,
                "clipped_effective_sample_ratio": 0.0,
                "effective_sample_basis": "clipped_importance_weights",
                "clipped_samples": 0,
                "clipped_share": 0.0,
                "logged_action_support_coverage": 0.0,
            },
        }

    point = _point_estimates(rows, weight_cap)
    raw_weights = point["raw_weights"]
    weights = point["weights"]
    raw_ess = _effective_sample_size(raw_weights)
    clipped_ess = _effective_sample_size(weights)
    clipped = sum(1 for raw in raw_weights if raw > weight_cap)
    supported = sum(1 for row in rows if row.target_propensity > 0.0)
    direct_samples = int(point["direct_samples"])
    baseline = float(point["logged_mean"])

    def estimator_row(name: str) -> dict[str, Any]:
        value = point.get(name)
        if value is None:
            return {
                "available": False,
                "value": None,
                "delta_vs_logged": None,
            }
        return {
            "available": True,
            "value": round(float(value), 6),
            "delta_vs_logged": round(float(value) - baseline, 6),
            "importance_weight_clipping_applied": clipped > 0,
        }

    estimators = {
        "logged_mean": {
            "available": True,
            "value": round(baseline, 6),
            "delta_vs_logged": 0.0,
        },
        "ips": estimator_row("ips"),
        "snips": estimator_row("snips"),
        "dr": {
            **estimator_row("dr"),
            "direct_model_samples": direct_samples,
            "direct_model_coverage": round(direct_samples / len(rows), 6),
        },
    }
    confidence = {
        name: _confidence_for_estimator(
            rows,
            estimator=name,
            weight_cap=weight_cap,
            iterations=bootstrap_iterations,
        )
        for name in ("ips", "snips", "dr")
    }
    return {
        "available": True,
        "samples": len(rows),
        "surface": rows[0].surface,
        "logging_policy_id": rows[0].logging_policy_id,
        "target_policy_id": rows[0].target_policy_id,
        "estimators": estimators,
        "confidence": confidence,
        "diagnostics": {
            "importance_weight_cap": weight_cap,
            "raw_weight_max": round(max(raw_weights), 6),
            "weight_max": round(max(weights), 6),
            "weight_mean": round(mean(weights), 6),
            # Compatibility fields keep their historical clipped-weight meaning.
            "effective_sample_size": round(clipped_ess, 6),
            "effective_sample_ratio": round(clipped_ess / len(rows), 6),
            "effective_sample_basis": "clipped_importance_weights",
            "raw_effective_sample_size": round(raw_ess, 6),
            "raw_effective_sample_ratio": round(raw_ess / len(rows), 6),
            "clipped_effective_sample_size": round(clipped_ess, 6),
            "clipped_effective_sample_ratio": round(clipped_ess / len(rows), 6),
            "clipped_samples": clipped,
            "clipped_share": round(clipped / len(rows), 6),
            "zero_target_samples": len(rows) - supported,
            "logged_action_support_coverage": round(supported / len(rows), 6),
            "logging_propensity_min": round(min(row.logging_propensity for row in rows), 8),
            "logging_propensity_max": round(max(row.logging_propensity for row in rows), 8),
            "target_propensity_mean": round(mean(row.target_propensity for row in rows), 8),
            "direct_model_samples": direct_samples,
            "direct_model_coverage": round(direct_samples / len(rows), 6),
        },
        "assumptions": [
            "one logged action per decision_id",
            "one surface, logging policy and target policy per report",
            "logging_propensity is mu(a_logged|x)",
            "target_propensity is pi(a_logged|x) for the same action",
            "target-policy support must overlap the logging policy",
            "importance-weight clipping trades variance for bias",
            "raw-weight ESS should be inspected before clipping; clipped ESS is also reported",
            "DR additionally requires q_hat(x,a_logged) and E_pi[q_hat(x,a)]",
        ],
    }


__all__ = ["CounterfactualRecord", "evaluate_off_policy"]
