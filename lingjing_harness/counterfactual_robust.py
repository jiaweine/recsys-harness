"""Robust doubly-robust off-policy evaluation for heavy-tailed importance weights.

The base ``counterfactual`` module intentionally provides a small explicit
IPS/SNIPS/DR surface.  This module adds a clean-room robust DR family inspired by
well-established OPE estimators without changing the base contract:

- Direct Method (DM)
- raw Doubly Robust (Raw-DR)
- Switch-DR
- optimistic-shrinkage DR (DRos-style weight shrinkage)

All estimators require the same explicit logged-action policy probabilities as the
base module.  The robust DR family additionally requires complete reward-model
inputs for every decision.  No probabilities or reward models are inferred from
ranking scores.

Parameter selection is deliberately described as a *stability routing heuristic*,
not a proof of optimal statistical tuning.  Candidate Switch thresholds and DRos
shrinkage parameters are evaluated with deterministic bootstrap variance plus a
squared-deviation proxy from Raw-DR.  The full candidate table is returned so a
product team can audit or override the selection.
"""

from __future__ import annotations

from hashlib import blake2b
from math import isfinite, sqrt
from random import Random
from statistics import mean
from typing import Any, Callable, Iterable

from .counterfactual import CounterfactualRecord


DEFAULT_BOOTSTRAP_ITERATIONS = 500
DEFAULT_SWITCH_TAU_GRID = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)
DEFAULT_DROS_LAMBDA_GRID = (0.0, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0)


def _ordered_rows(records: Iterable[CounterfactualRecord]) -> list[CounterfactualRecord]:
    rows = sorted(list(records), key=lambda row: row.decision_id)
    seen: set[str] = set()
    for row in rows:
        if row.decision_id in seen:
            raise ValueError("robust OPE requires exactly one logged action per decision_id")
        seen.add(row.decision_id)
    if rows:
        anchor = rows[0]
        for row in rows[1:]:
            if row.surface != anchor.surface:
                raise ValueError("robust OPE report must contain exactly one surface")
            if row.logging_policy_id != anchor.logging_policy_id:
                raise ValueError("robust OPE report must contain exactly one logging_policy_id")
            if row.target_policy_id != anchor.target_policy_id:
                raise ValueError("robust OPE report must contain exactly one target_policy_id")
    return rows


def _has_complete_reward_model(rows: list[CounterfactualRecord]) -> bool:
    return bool(rows) and all(
        row.logged_reward_estimate is not None
        and row.target_reward_estimate is not None
        for row in rows
    )


def _raw_weight(row: CounterfactualRecord) -> float:
    return float(row.target_propensity) / float(row.logging_propensity)


def _logged_mean(rows: list[CounterfactualRecord]) -> float:
    return mean(float(row.reward) for row in rows) if rows else 0.0


def direct_method(rows: Iterable[CounterfactualRecord]) -> float:
    ordered = _ordered_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("direct method requires complete reward-model inputs")
    return mean(float(row.target_reward_estimate) for row in ordered)


def raw_doubly_robust(rows: Iterable[CounterfactualRecord]) -> float:
    ordered = _ordered_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("raw DR requires complete reward-model inputs")
    return mean(
        float(row.target_reward_estimate)
        + _raw_weight(row)
        * (float(row.reward) - float(row.logged_reward_estimate))
        for row in ordered
    )


def switch_doubly_robust(
    rows: Iterable[CounterfactualRecord],
    *,
    tau: float,
) -> float:
    """Switch-DR: use the DR correction only when the raw weight is <= tau."""

    ordered = _ordered_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("Switch-DR requires complete reward-model inputs")
    tau = float(tau)
    if not isfinite(tau) or tau < 0.0:
        raise ValueError("Switch-DR tau must be finite and >= 0")
    return mean(
        float(row.target_reward_estimate)
        + (
            _raw_weight(row)
            * (float(row.reward) - float(row.logged_reward_estimate))
            if _raw_weight(row) <= tau
            else 0.0
        )
        for row in ordered
    )


def _dros_weight(weight: float, lambda_: float) -> float:
    lambda_ = float(lambda_)
    if lambda_ <= 0.0:
        return 0.0
    return (lambda_ / (weight * weight + lambda_)) * weight


def dros_doubly_robust(
    rows: Iterable[CounterfactualRecord],
    *,
    lambda_: float,
) -> float:
    """DRos-style optimistic shrinkage of the raw importance correction."""

    ordered = _ordered_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("DRos requires complete reward-model inputs")
    lambda_ = float(lambda_)
    if not isfinite(lambda_) or lambda_ < 0.0:
        raise ValueError("DRos lambda must be finite and >= 0")
    return mean(
        float(row.target_reward_estimate)
        + _dros_weight(_raw_weight(row), lambda_)
        * (float(row.reward) - float(row.logged_reward_estimate))
        for row in ordered
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    fraction = max(0.0, min(1.0, float(fraction)))
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(len(ordered) - 1, low + 1)
    interpolation = position - low
    return ordered[low] * (1.0 - interpolation) + ordered[high] * interpolation


def _effective_sample_ratio(weights: list[float]) -> float:
    if not weights:
        return 0.0
    total = sum(weights)
    squares = sum(value * value for value in weights)
    if squares <= 0.0:
        return 0.0
    return (total * total / squares) / len(weights)


def _coefficient_of_variation(values: list[float]) -> float:
    if not values:
        return 0.0
    avg = mean(values)
    if avg == 0.0:
        return 0.0
    variance = mean((value - avg) ** 2 for value in values)
    return sqrt(max(0.0, variance)) / abs(avg)


def _stable_seed(rows: list[CounterfactualRecord], label: str) -> int:
    raw = "|".join(
        f"{row.decision_id}:{row.reward:.10g}:{row.logging_propensity:.10g}:"
        f"{row.target_propensity:.10g}:{row.logged_reward_estimate}:{row.target_reward_estimate}"
        for row in rows
    )
    return int.from_bytes(
        blake2b(f"{label}|{raw}".encode("utf-8"), digest_size=8).digest(),
        "little",
    )


def _bootstrap_draws(
    rows: list[CounterfactualRecord],
    estimator: Callable[[list[CounterfactualRecord]], float],
    *,
    label: str,
    iterations: int,
) -> list[float]:
    if len(rows) < 2:
        return []
    rng = Random(_stable_seed(rows, label))
    count = len(rows)
    draws: list[float] = []
    for _ in range(max(100, min(5000, int(iterations)))):
        sample = [rows[rng.randrange(count)] for _ in range(count)]
        draws.append(float(estimator(sample)))
    return draws


def _confidence(
    rows: list[CounterfactualRecord],
    estimator: Callable[[list[CounterfactualRecord]], float],
    *,
    label: str,
    iterations: int,
) -> dict[str, Any]:
    value = float(estimator(rows))
    baseline = _logged_mean(rows)
    delta = value - baseline
    draws = _bootstrap_draws(
        rows,
        estimator,
        label=label,
        iterations=iterations,
    )
    if not draws:
        return {
            "available": False,
            "samples": len(rows),
            "delta": round(delta, 6),
            "ci95": None,
            "probability_positive": None,
        }
    delta_draws = sorted(draw - baseline for draw in draws)
    low = delta_draws[max(0, int(len(delta_draws) * 0.025) - 1)]
    high = delta_draws[min(len(delta_draws) - 1, int(len(delta_draws) * 0.975))]
    probability_positive = sum(1 for draw in delta_draws if draw > 0.0) / len(delta_draws)
    return {
        "available": True,
        "samples": len(rows),
        "delta": round(delta, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "probability_positive": round(probability_positive, 4),
    }


def _empirical_switch_grid(weights: list[float]) -> list[float]:
    values = [*DEFAULT_SWITCH_TAU_GRID]
    for fraction in (0.50, 0.75, 0.90, 0.95, 1.0):
        values.append(_percentile(weights, fraction))
    return sorted({round(max(0.0, value), 8) for value in values})


def _empirical_dros_grid(weights: list[float]) -> list[float]:
    values = [*DEFAULT_DROS_LAMBDA_GRID]
    for fraction in (0.50, 0.75, 0.90, 0.95, 1.0):
        weight = _percentile(weights, fraction)
        values.append(weight * weight)
    return sorted({round(max(0.0, value), 8) for value in values})


def _variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return mean((value - avg) ** 2 for value in values)


def _select_stable_parameter(
    rows: list[CounterfactualRecord],
    *,
    family: str,
    candidates: list[float],
    raw_dr_value: float,
    iterations: int,
) -> tuple[float, list[dict[str, Any]]]:
    """Route to a bias/variance compromise; return all evidence for auditability."""

    table: list[dict[str, Any]] = []
    for value in candidates:
        if family == "switch_dr":
            estimator = lambda sample, value=value: switch_doubly_robust(sample, tau=value)
        elif family == "dros":
            estimator = lambda sample, value=value: dros_doubly_robust(sample, lambda_=value)
        else:  # pragma: no cover - internal contract
            raise ValueError("unknown robust estimator family")
        estimate = float(estimator(rows))
        draws = _bootstrap_draws(
            rows,
            estimator,
            label=f"tune:{family}:{value:.12g}",
            iterations=max(100, min(1000, iterations // 2)),
        )
        variance = _variance(draws)
        bias_proxy = (estimate - raw_dr_value) ** 2
        risk_proxy = variance + bias_proxy
        table.append(
            {
                "parameter": value,
                "estimate": round(estimate, 8),
                "bootstrap_variance": round(variance, 10),
                "squared_raw_dr_deviation": round(bias_proxy, 10),
                "stability_risk_proxy": round(risk_proxy, 10),
            }
        )
    table.sort(key=lambda row: (float(row["stability_risk_proxy"]), float(row["parameter"])))
    return float(table[0]["parameter"]), table


def evaluate_robust_off_policy(
    records: Iterable[CounterfactualRecord],
    *,
    switch_tau: float | None = None,
    dros_lambda: float | None = None,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Evaluate a robust DR family and report overlap/tail/agreement diagnostics."""

    rows = _ordered_rows(records)
    if not rows:
        return {
            "available": False,
            "samples": 0,
            "estimators": {},
            "confidence": {},
            "diagnostics": {},
            "tuning": {},
        }
    if not _has_complete_reward_model(rows):
        return {
            "available": False,
            "samples": len(rows),
            "surface": rows[0].surface,
            "logging_policy_id": rows[0].logging_policy_id,
            "target_policy_id": rows[0].target_policy_id,
            "estimators": {},
            "confidence": {},
            "diagnostics": {
                "reward_model_coverage": sum(
                    1
                    for row in rows
                    if row.logged_reward_estimate is not None
                    and row.target_reward_estimate is not None
                )
                / len(rows),
            },
            "tuning": {},
            "reason": "robust DR estimators require complete reward-model inputs",
        }

    weights = [_raw_weight(row) for row in rows]
    raw_dr_value = raw_doubly_robust(rows)
    dm_value = direct_method(rows)

    if switch_tau is None:
        selected_tau, switch_table = _select_stable_parameter(
            rows,
            family="switch_dr",
            candidates=_empirical_switch_grid(weights),
            raw_dr_value=raw_dr_value,
            iterations=bootstrap_iterations,
        )
    else:
        selected_tau = float(switch_tau)
        if not isfinite(selected_tau) or selected_tau < 0.0:
            raise ValueError("switch_tau must be finite and >= 0")
        switch_table = []

    if dros_lambda is None:
        selected_lambda, dros_table = _select_stable_parameter(
            rows,
            family="dros",
            candidates=_empirical_dros_grid(weights),
            raw_dr_value=raw_dr_value,
            iterations=bootstrap_iterations,
        )
    else:
        selected_lambda = float(dros_lambda)
        if not isfinite(selected_lambda) or selected_lambda < 0.0:
            raise ValueError("dros_lambda must be finite and >= 0")
        dros_table = []

    switch_value = switch_doubly_robust(rows, tau=selected_tau)
    dros_value = dros_doubly_robust(rows, lambda_=selected_lambda)
    baseline = _logged_mean(rows)

    def estimator_row(value: float, **metadata: Any) -> dict[str, Any]:
        return {
            "available": True,
            "value": round(float(value), 6),
            "delta_vs_logged": round(float(value) - baseline, 6),
            **metadata,
        }

    estimators = {
        "dm": estimator_row(dm_value),
        "raw_dr": estimator_row(raw_dr_value),
        "switch_dr": estimator_row(switch_value, tau=selected_tau),
        "dros": estimator_row(dros_value, lambda_=selected_lambda),
    }
    confidence = {
        "raw_dr": _confidence(
            rows,
            lambda sample: raw_doubly_robust(sample),
            label="raw_dr",
            iterations=bootstrap_iterations,
        ),
        "switch_dr": _confidence(
            rows,
            lambda sample: switch_doubly_robust(sample, tau=selected_tau),
            label=f"switch_dr:{selected_tau:.12g}",
            iterations=bootstrap_iterations,
        ),
        "dros": _confidence(
            rows,
            lambda sample: dros_doubly_robust(sample, lambda_=selected_lambda),
            label=f"dros:{selected_lambda:.12g}",
            iterations=bootstrap_iterations,
        ),
    }

    robust_values = [raw_dr_value, switch_value, dros_value]
    spread = max(robust_values) - min(robust_values)
    p95 = _percentile(weights, 0.95)
    raw_ess_ratio = _effective_sample_ratio(weights)
    weight_cv = _coefficient_of_variation(weights)
    heavy_tail = bool(
        max(weights, default=0.0) >= 20.0
        or p95 >= 10.0
        or raw_ess_ratio < 0.35
        or weight_cv >= 1.5
    )

    # This recommendation is a routing heuristic only. The experiment contract may
    # still explicitly choose a different primary estimator.
    switch_risk = (
        float(switch_table[0]["stability_risk_proxy"])
        if switch_table
        else None
    )
    dros_risk = (
        float(dros_table[0]["stability_risk_proxy"])
        if dros_table
        else None
    )
    if heavy_tail:
        candidate_risks = [
            ("switch_dr", switch_risk),
            ("dros", dros_risk),
        ]
        finite_risks = [row for row in candidate_risks if row[1] is not None]
        recommended = min(finite_risks, key=lambda row: (float(row[1]), row[0]))[0] if finite_risks else "dros"
    else:
        recommended = "raw_dr"

    return {
        "available": True,
        "samples": len(rows),
        "surface": rows[0].surface,
        "logging_policy_id": rows[0].logging_policy_id,
        "target_policy_id": rows[0].target_policy_id,
        "estimators": estimators,
        "confidence": confidence,
        "diagnostics": {
            "raw_weight_max": round(max(weights), 6),
            "raw_weight_p95": round(p95, 6),
            "raw_weight_cv": round(weight_cv, 6),
            "raw_effective_sample_ratio": round(raw_ess_ratio, 6),
            "heavy_tail_detected": heavy_tail,
            "robust_estimator_spread": round(spread, 6),
            "robust_estimator_agreement": round(1.0 / (1.0 + abs(spread)), 6),
            "recommended_estimator": recommended,
            "recommendation_semantics": "stability_routing_heuristic_not_promotion_authority",
        },
        "tuning": {
            "method": "deterministic_bootstrap_variance_plus_raw_dr_deviation_proxy",
            "claim": "stability routing heuristic; not statistically optimal hyperparameter proof",
            "switch_dr": {
                "selected_tau": selected_tau,
                "automatic": switch_tau is None,
                "candidates": switch_table,
            },
            "dros": {
                "selected_lambda": selected_lambda,
                "automatic": dros_lambda is None,
                "candidates": dros_table,
            },
        },
    }


__all__ = [
    "direct_method",
    "raw_doubly_robust",
    "switch_doubly_robust",
    "dros_doubly_robust",
    "evaluate_robust_off_policy",
]
