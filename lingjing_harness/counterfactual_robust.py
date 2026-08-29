"""Robust doubly-robust off-policy evaluation for heavy-tailed importance weights.

This module extends the explicit IPS/SNIPS/DR surface with a clean-room robust
DR family while preserving the repository's causal-evidence rules:

- Direct Method (DM)
- raw Doubly Robust (Raw-DR)
- Switch-DR
- optimistic-shrinkage DR (DRos-style weight shrinkage)

The public estimator entry points validate decision identity, surface, policy
identity, and reward-model completeness exactly once. Bootstrap resamples then
operate on already validated rows and are *allowed* to contain repeated sampled
indices, as a correct non-parametric bootstrap requires.

Automatic Switch/DRos parameters are selected with an auditable empirical
bias/variance stability proxy. For confidence intervals, automatically tuned
estimators re-select their parameter inside every bootstrap resample so tuning
uncertainty is not silently treated as fixed. Each bootstrap delta is measured
against that resample's own logged-policy mean.

The tuning score remains a stability-routing heuristic, not a proof that a
hyperparameter is statistically optimal and never grants activation authority.
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


def _validated_rows(records: Iterable[CounterfactualRecord]) -> list[CounterfactualRecord]:
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


def _dm_on_rows(rows: list[CounterfactualRecord]) -> float:
    return mean(float(row.target_reward_estimate) for row in rows)


def _raw_dr_contribution(row: CounterfactualRecord) -> float:
    return (
        float(row.target_reward_estimate)
        + _raw_weight(row)
        * (float(row.reward) - float(row.logged_reward_estimate))
    )


def _raw_dr_on_rows(rows: list[CounterfactualRecord]) -> float:
    return mean(_raw_dr_contribution(row) for row in rows)


def _switch_contribution(row: CounterfactualRecord, tau: float) -> float:
    weight = _raw_weight(row)
    correction = (
        weight * (float(row.reward) - float(row.logged_reward_estimate))
        if weight <= tau
        else 0.0
    )
    return float(row.target_reward_estimate) + correction


def _switch_on_rows(rows: list[CounterfactualRecord], tau: float) -> float:
    return mean(_switch_contribution(row, tau) for row in rows)


def _dros_weight(weight: float, lambda_: float) -> float:
    if lambda_ <= 0.0:
        return 0.0
    return (lambda_ / (weight * weight + lambda_)) * weight


def _dros_contribution(row: CounterfactualRecord, lambda_: float) -> float:
    return (
        float(row.target_reward_estimate)
        + _dros_weight(_raw_weight(row), lambda_)
        * (float(row.reward) - float(row.logged_reward_estimate))
    )


def _dros_on_rows(rows: list[CounterfactualRecord], lambda_: float) -> float:
    return mean(_dros_contribution(row, lambda_) for row in rows)


def direct_method(rows: Iterable[CounterfactualRecord]) -> float:
    ordered = _validated_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("direct method requires complete reward-model inputs")
    return _dm_on_rows(ordered)


def raw_doubly_robust(rows: Iterable[CounterfactualRecord]) -> float:
    ordered = _validated_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("raw DR requires complete reward-model inputs")
    return _raw_dr_on_rows(ordered)


def switch_doubly_robust(
    rows: Iterable[CounterfactualRecord],
    *,
    tau: float,
) -> float:
    """Switch-DR: apply the importance correction only for raw weight <= tau."""

    ordered = _validated_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("Switch-DR requires complete reward-model inputs")
    tau = float(tau)
    if not isfinite(tau) or tau < 0.0:
        raise ValueError("Switch-DR tau must be finite and >= 0")
    return _switch_on_rows(ordered, tau)


def dros_doubly_robust(
    rows: Iterable[CounterfactualRecord],
    *,
    lambda_: float,
) -> float:
    """DRos-style optimistic shrinkage of the raw importance correction."""

    ordered = _validated_rows(rows)
    if not _has_complete_reward_model(ordered):
        raise ValueError("DRos requires complete reward-model inputs")
    lambda_ = float(lambda_)
    if not isfinite(lambda_) or lambda_ < 0.0:
        raise ValueError("DRos lambda must be finite and >= 0")
    return _dros_on_rows(ordered, lambda_)


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


def _sample_variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return sum((value - avg) ** 2 for value in values) / (len(values) - 1)


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


def _bootstrap_delta_draws(
    rows: list[CounterfactualRecord],
    estimator: Callable[[list[CounterfactualRecord]], float],
    *,
    label: str,
    iterations: int,
) -> list[float]:
    """Bootstrap estimator-minus-logged deltas using sampled row indices.

    Duplicate decision identities are expected in a non-parametric bootstrap and
    therefore must not be passed back through the external uniqueness validator.
    """

    if len(rows) < 2:
        return []
    rng = Random(_stable_seed(rows, label))
    count = len(rows)
    draws: list[float] = []
    for _ in range(max(100, min(5000, int(iterations)))):
        sample = [rows[rng.randrange(count)] for _ in range(count)]
        draws.append(float(estimator(sample)) - _logged_mean(sample))
    return draws


def _confidence(
    rows: list[CounterfactualRecord],
    estimator: Callable[[list[CounterfactualRecord]], float],
    *,
    label: str,
    iterations: int,
    tuning_semantics: str = "fixed",
) -> dict[str, Any]:
    value = float(estimator(rows))
    delta = value - _logged_mean(rows)
    draws = _bootstrap_delta_draws(
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
            "bootstrap_baseline": "resampled_logged_mean",
            "tuning_semantics": tuning_semantics,
        }
    draws.sort()
    low = draws[max(0, int(len(draws) * 0.025) - 1)]
    high = draws[min(len(draws) - 1, int(len(draws) * 0.975))]
    probability_positive = sum(1 for draw in draws if draw > 0.0) / len(draws)
    return {
        "available": True,
        "samples": len(rows),
        "delta": round(delta, 6),
        "ci95": [round(low, 6), round(high, 6)],
        "probability_positive": round(probability_positive, 4),
        "bootstrap_baseline": "resampled_logged_mean",
        "tuning_semantics": tuning_semantics,
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


def _select_stable_parameter(
    rows: list[CounterfactualRecord],
    *,
    family: str,
    candidates: list[float],
) -> tuple[float, list[dict[str, Any]]]:
    """Choose a transparent empirical bias/variance compromise.

    The variance term is the plug-in variance of the estimator mean. The bias
    proxy is squared deviation from Raw-DR. It is intentionally exposed as a
    heuristic, not a hidden claim of MSE-optimal tuning.
    """

    raw_dr_value = _raw_dr_on_rows(rows)
    table: list[dict[str, Any]] = []
    for value in candidates:
        if family == "switch_dr":
            contributions = [_switch_contribution(row, value) for row in rows]
        elif family == "dros":
            contributions = [_dros_contribution(row, value) for row in rows]
        else:  # pragma: no cover - internal contract
            raise ValueError("unknown robust estimator family")
        estimate = mean(contributions)
        variance_proxy = _sample_variance(contributions) / max(1, len(contributions))
        bias_proxy = (estimate - raw_dr_value) ** 2
        risk_proxy = variance_proxy + bias_proxy
        table.append(
            {
                "parameter": value,
                "estimate": round(estimate, 8),
                "sampling_variance_proxy": round(variance_proxy, 10),
                "squared_raw_dr_deviation": round(bias_proxy, 10),
                "stability_risk_proxy": round(risk_proxy, 10),
            }
        )
    table.sort(key=lambda row: (float(row["stability_risk_proxy"]), float(row["parameter"])))
    return float(table[0]["parameter"]), table


def _auto_switch_estimator(sample: list[CounterfactualRecord]) -> float:
    weights = [_raw_weight(row) for row in sample]
    tau, _ = _select_stable_parameter(
        sample,
        family="switch_dr",
        candidates=_empirical_switch_grid(weights),
    )
    return _switch_on_rows(sample, tau)


def _auto_dros_estimator(sample: list[CounterfactualRecord]) -> float:
    weights = [_raw_weight(row) for row in sample]
    lambda_, _ = _select_stable_parameter(
        sample,
        family="dros",
        candidates=_empirical_dros_grid(weights),
    )
    return _dros_on_rows(sample, lambda_)


def evaluate_robust_off_policy(
    records: Iterable[CounterfactualRecord],
    *,
    switch_tau: float | None = None,
    dros_lambda: float | None = None,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> dict[str, Any]:
    """Evaluate robust DR estimators plus overlap, tail, and agreement diagnostics."""

    rows = _validated_rows(records)
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
    raw_dr_value = _raw_dr_on_rows(rows)
    dm_value = _dm_on_rows(rows)

    if switch_tau is None:
        selected_tau, switch_table = _select_stable_parameter(
            rows,
            family="switch_dr",
            candidates=_empirical_switch_grid(weights),
        )
        switch_estimator = _auto_switch_estimator
        switch_tuning_semantics = "retuned_within_each_bootstrap_resample"
    else:
        selected_tau = float(switch_tau)
        if not isfinite(selected_tau) or selected_tau < 0.0:
            raise ValueError("switch_tau must be finite and >= 0")
        switch_table = []
        switch_estimator = lambda sample: _switch_on_rows(sample, selected_tau)
        switch_tuning_semantics = "fixed_user_parameter"

    if dros_lambda is None:
        selected_lambda, dros_table = _select_stable_parameter(
            rows,
            family="dros",
            candidates=_empirical_dros_grid(weights),
        )
        dros_estimator = _auto_dros_estimator
        dros_tuning_semantics = "retuned_within_each_bootstrap_resample"
    else:
        selected_lambda = float(dros_lambda)
        if not isfinite(selected_lambda) or selected_lambda < 0.0:
            raise ValueError("dros_lambda must be finite and >= 0")
        dros_table = []
        dros_estimator = lambda sample: _dros_on_rows(sample, selected_lambda)
        dros_tuning_semantics = "fixed_user_parameter"

    switch_value = _switch_on_rows(rows, selected_tau)
    dros_value = _dros_on_rows(rows, selected_lambda)
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
            _raw_dr_on_rows,
            label="raw_dr",
            iterations=bootstrap_iterations,
        ),
        "switch_dr": _confidence(
            rows,
            switch_estimator,
            label=(
                "switch_dr:auto"
                if switch_tau is None
                else f"switch_dr:fixed:{selected_tau:.12g}"
            ),
            iterations=bootstrap_iterations,
            tuning_semantics=switch_tuning_semantics,
        ),
        "dros": _confidence(
            rows,
            dros_estimator,
            label=(
                "dros:auto"
                if dros_lambda is None
                else f"dros:fixed:{selected_lambda:.12g}"
            ),
            iterations=bootstrap_iterations,
            tuning_semantics=dros_tuning_semantics,
        ),
    }

    robust_values = [raw_dr_value, switch_value, dros_value]
    spread = max(robust_values) - min(robust_values)
    p95 = _percentile(weights, 0.95)
    raw_ess_ratio = _effective_sample_ratio(weights)
    weight_cv = _coefficient_of_variation(weights)
    max_weight = max(weights, default=0.0)
    heavy_tail = bool(
        max_weight >= 20.0
        or p95 >= 10.0
        or raw_ess_ratio < 0.35
        or weight_cv >= 1.5
    )

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
        recommended = (
            min(finite_risks, key=lambda row: (float(row[1]), row[0]))[0]
            if finite_risks
            else "dros"
        )
    else:
        recommended = "raw_dr"

    value_scale = max(
        1.0,
        abs(baseline),
        *(abs(value) for value in robust_values),
    )
    normalized_spread = spread / value_scale

    return {
        "available": True,
        "samples": len(rows),
        "surface": rows[0].surface,
        "logging_policy_id": rows[0].logging_policy_id,
        "target_policy_id": rows[0].target_policy_id,
        "estimators": estimators,
        "confidence": confidence,
        "diagnostics": {
            "reward_model_coverage": 1.0,
            "raw_weight_max": round(max_weight, 6),
            "raw_weight_p95": round(p95, 6),
            "raw_weight_cv": round(weight_cv, 6),
            "raw_effective_sample_ratio": round(raw_ess_ratio, 6),
            "heavy_tail_detected": heavy_tail,
            "robust_estimator_spread": round(spread, 6),
            "robust_estimator_normalized_spread": round(normalized_spread, 6),
            "robust_estimator_agreement": round(1.0 / (1.0 + normalized_spread), 6),
            "recommended_estimator": recommended,
            "recommendation_semantics": "stability_routing_heuristic_not_promotion_authority",
        },
        "tuning": {
            "method": "plug_in_sampling_variance_plus_squared_raw_dr_deviation",
            "claim": "stability routing heuristic; not statistically optimal hyperparameter proof",
            "bootstrap": "automatic parameters are retuned inside every resample",
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
