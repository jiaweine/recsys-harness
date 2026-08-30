"""Anytime-valid online experiment statistics and guarded ramp decisions.

This module intentionally owns *evidence and recommendations*, not production
activation. A controlled experiment may recommend a ramp, hold, or rollback; even a
fully successful final ramp only becomes eligible for promotion review.

Statistical contracts:
- sample-ratio mismatch (SRM) uses a Dirichlet-multinomial mixture likelihood-ratio
  e-process. Expected allocation is epoch-specific, so 1% -> 5% -> 25% ramps are not
  incorrectly tested against one global split. The historical maximum e-value is
  reconstructed from ordered assignment observations, preserving an anytime alarm.
- Bernoulli outcomes use beta-binomial mixture confidence sequences inverted from
  e-values. General [0, 1] outcomes use a conservative time-uniform Hoeffding
  alpha-spending confidence sequence.
- outcome alpha is split across all decision metrics and both arms, providing a
  familywise simultaneous guarantee for the reported effect intervals under the
  model assumptions.
- outcome decisions use only the current allocation epoch. Earlier ramp epochs are
  retained for SRM/audit evidence but are not pooled into the new epoch's effect
  interval, avoiding time-drift / allocation-weighting bias across ramp changes.
- CUPED is diagnostic only here. Adjusted values can leave [0, 1], so variance
  reduction cannot silently enter the bounded anytime-valid decision path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp, isfinite, lgamma, log, pi, sqrt
from statistics import fmean
from typing import Any, Iterable, Mapping


_EPSILON = 1e-12


def _require_probability(name: str, value: float, *, open_interval: bool = False) -> float:
    number = float(value)
    valid = 0.0 < number < 1.0 if open_interval else 0.0 <= number <= 1.0
    if not isfinite(number) or not valid:
        interval = "(0, 1)" if open_interval else "[0, 1]"
        raise ValueError(f"{name} must be within {interval}")
    return number


@dataclass(frozen=True, slots=True)
class OnlineMetricSpec:
    """One bounded decision metric expressed as positive-is-better effect evidence."""

    name: str
    role: str  # primary | guardrail
    kind: str  # bernoulli | bounded
    direction: str  # higher_is_better | lower_is_better
    advance_threshold: float
    rollback_threshold: float
    minimum_samples_per_arm: int = 50
    cuped_covariate: str | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("online metric name must not be empty")
        if self.role not in {"primary", "guardrail"}:
            raise ValueError("online metric role must be primary or guardrail")
        if self.kind not in {"bernoulli", "bounded"}:
            raise ValueError("online metric kind must be bernoulli or bounded")
        if self.direction not in {"higher_is_better", "lower_is_better"}:
            raise ValueError(
                "online metric direction must be higher_is_better or lower_is_better"
            )
        if int(self.minimum_samples_per_arm) < 1:
            raise ValueError("minimum_samples_per_arm must be >= 1")
        for name, value in (
            ("advance_threshold", self.advance_threshold),
            ("rollback_threshold", self.rollback_threshold),
        ):
            if not isfinite(float(value)):
                raise ValueError(f"{name} must be finite")
        if float(self.rollback_threshold) > float(self.advance_threshold):
            raise ValueError("rollback_threshold must be <= advance_threshold")
        if self.cuped_covariate is not None and not self.cuped_covariate.strip():
            raise ValueError("cuped_covariate must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class RampStage:
    stage_index: int
    candidate_fraction: float
    minimum_randomized_units: int

    def __post_init__(self) -> None:
        if int(self.stage_index) < 0:
            raise ValueError("stage_index must be >= 0")
        _require_probability(
            "candidate_fraction", self.candidate_fraction, open_interval=True
        )
        if int(self.minimum_randomized_units) < 2:
            raise ValueError("minimum_randomized_units must be >= 2")


@dataclass(frozen=True, slots=True)
class AllocationEpoch:
    epoch_id: str
    stage_index: int
    candidate_fraction: float

    def __post_init__(self) -> None:
        if not self.epoch_id.strip():
            raise ValueError("epoch_id must not be empty")
        if int(self.stage_index) < 0:
            raise ValueError("epoch stage_index must be >= 0")
        _require_probability(
            "epoch candidate_fraction", self.candidate_fraction, open_interval=True
        )


@dataclass(frozen=True, slots=True)
class OnlineExperimentSpec:
    experiment_id: str
    control_arm: str
    candidate_arm: str
    metrics: tuple[OnlineMetricSpec, ...]
    stages: tuple[RampStage, ...]
    outcome_alpha: float = 0.05
    srm_alpha: float = 0.01
    srm_dirichlet_prior: float = 0.5

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("online experiment_id must not be empty")
        if not self.control_arm.strip() or not self.candidate_arm.strip():
            raise ValueError("control_arm and candidate_arm are required")
        if self.control_arm == self.candidate_arm:
            raise ValueError("control_arm and candidate_arm must differ")
        if not self.metrics:
            raise ValueError("online experiment requires decision metrics")
        names = [metric.name for metric in self.metrics]
        if len(names) != len(set(names)):
            raise ValueError("online metric names must be unique")
        if sum(metric.role == "primary" for metric in self.metrics) != 1:
            raise ValueError("online experiment requires exactly one primary metric")
        if not self.stages:
            raise ValueError("online experiment requires ramp stages")
        expected_indices = list(range(len(self.stages)))
        if [int(stage.stage_index) for stage in self.stages] != expected_indices:
            raise ValueError("ramp stage indices must be contiguous from zero")
        fractions = [float(stage.candidate_fraction) for stage in self.stages]
        if any(later <= earlier for earlier, later in zip(fractions, fractions[1:])):
            raise ValueError("candidate ramp fractions must be strictly increasing")
        _require_probability("outcome_alpha", self.outcome_alpha, open_interval=True)
        _require_probability("srm_alpha", self.srm_alpha, open_interval=True)
        prior = float(self.srm_dirichlet_prior)
        if not isfinite(prior) or prior <= 0.0:
            raise ValueError("srm_dirichlet_prior must be finite and > 0")

    @property
    def primary_metric(self) -> OnlineMetricSpec:
        return next(metric for metric in self.metrics if metric.role == "primary")


@dataclass(frozen=True, slots=True)
class OnlineObservation:
    """One randomized unit with unit-level matured bounded outcomes.

    ``sequence`` is assignment order, not event-arrival order. Metric values may be
    absent while a delayed outcome matures; SRM still counts the randomized unit.
    """

    unit_id: str
    sequence: int
    epoch_id: str
    arm: str
    metrics: Mapping[str, float] = field(default_factory=dict)
    pre_exposure: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.unit_id.strip():
            raise ValueError("online observation unit_id must not be empty")
        if int(self.sequence) < 0:
            raise ValueError("online observation sequence must be >= 0")
        if not self.epoch_id.strip() or not self.arm.strip():
            raise ValueError("online observation epoch_id and arm are required")


@dataclass(frozen=True, slots=True)
class ConfidenceSequence:
    estimate: float
    lower: float
    upper: float
    samples: int
    method: str
    alpha: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "samples": self.samples,
            "method": self.method,
            "alpha": self.alpha,
        }


def _log_beta(a: float, b: float) -> float:
    return lgamma(a) + lgamma(b) - lgamma(a + b)


def _bernoulli_log_mixture_e(
    successes: int,
    trials: int,
    null_mean: float,
    *,
    prior_a: float = 0.5,
    prior_b: float = 0.5,
) -> float:
    failures = int(trials) - int(successes)
    p = float(null_mean)
    log_mixture = (
        _log_beta(prior_a + successes, prior_b + failures)
        - _log_beta(prior_a, prior_b)
    )
    if p <= 0.0:
        return float("inf") if successes else log_mixture
    if p >= 1.0:
        return float("inf") if failures else log_mixture
    log_null = successes * log(p) + failures * log1p_negative(p)
    return log_mixture - log_null


def log1p_negative(value: float) -> float:
    """Stable log(1-x) on [0, 1]."""

    x = float(value)
    if x >= 1.0:
        return float("-inf")
    if x <= 0.0:
        return 0.0
    # log1p is intentionally avoided as a separate import to keep the public math
    # surface compact; near one, log(1-x) remains stable enough for our bisection.
    return log(1.0 - x)


def _bisect_boundary(
    function: Any,
    low: float,
    high: float,
    *,
    low_is_rejected: bool,
    iterations: int = 70,
) -> float:
    lo = float(low)
    hi = float(high)
    for _ in range(iterations):
        mid = (lo + hi) / 2.0
        rejected = function(mid) > 0.0
        if low_is_rejected:
            if rejected:
                lo = mid
            else:
                hi = mid
        else:
            if rejected:
                hi = mid
            else:
                lo = mid
    return hi if low_is_rejected else lo


def bernoulli_mixture_confidence_sequence(
    values: Iterable[float],
    *,
    alpha: float,
) -> ConfidenceSequence:
    """Invert a Jeffreys beta-binomial mixture e-process for a Bernoulli mean."""

    alpha = _require_probability("alpha", alpha, open_interval=True)
    rows = [float(value) for value in values]
    for value in rows:
        if value not in {0.0, 1.0}:
            raise ValueError("bernoulli metric values must be exactly 0 or 1")
    n = len(rows)
    if n == 0:
        return ConfidenceSequence(0.5, 0.0, 1.0, 0, "beta_binomial_mixture", alpha)
    successes = int(sum(rows))
    estimate = successes / n
    threshold = log(1.0 / alpha)

    def rejection_score(p: float) -> float:
        return _bernoulli_log_mixture_e(successes, n, p) - threshold

    if successes == 0:
        lower = 0.0
    else:
        lower = _bisect_boundary(
            rejection_score,
            0.0,
            estimate,
            low_is_rejected=True,
        )
    if successes == n:
        upper = 1.0
    else:
        upper = _bisect_boundary(
            rejection_score,
            estimate,
            1.0,
            low_is_rejected=False,
        )
    return ConfidenceSequence(
        estimate=estimate,
        lower=max(0.0, min(1.0, lower)),
        upper=max(0.0, min(1.0, upper)),
        samples=n,
        method="beta_binomial_mixture",
        alpha=alpha,
    )


def bounded_alpha_spending_confidence_sequence(
    values: Iterable[float],
    *,
    alpha: float,
) -> ConfidenceSequence:
    """Conservative time-uniform Hoeffding CS for observations in [0, 1]."""

    alpha = _require_probability("alpha", alpha, open_interval=True)
    rows = [float(value) for value in values]
    for value in rows:
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("bounded metric values must be finite within [0, 1]")
    n = len(rows)
    if n == 0:
        return ConfidenceSequence(0.5, 0.0, 1.0, 0, "hoeffding_alpha_spending", alpha)
    estimate = fmean(rows)
    # sum_n 6/(pi^2 n^2) = 1. Each fixed-time interval gets alpha_n and
    # Hoeffding's two-sided tail is bounded by alpha_n; union bound gives a CS.
    alpha_n = alpha * 6.0 / (pi * pi * n * n)
    radius = sqrt(log(2.0 / alpha_n) / (2.0 * n))
    return ConfidenceSequence(
        estimate=estimate,
        lower=max(0.0, estimate - radius),
        upper=min(1.0, estimate + radius),
        samples=n,
        method="hoeffding_alpha_spending",
        alpha=alpha,
    )


def _arm_confidence_sequence(
    values: list[float],
    metric: OnlineMetricSpec,
    *,
    alpha: float,
) -> ConfidenceSequence:
    if metric.kind == "bernoulli":
        return bernoulli_mixture_confidence_sequence(values, alpha=alpha)
    return bounded_alpha_spending_confidence_sequence(values, alpha=alpha)


def _effect_confidence_sequence(
    control: list[float],
    candidate: list[float],
    metric: OnlineMetricSpec,
    *,
    alpha: float,
) -> dict[str, Any]:
    # Split metric-level alpha across arms; a union bound makes the derived
    # difference interval simultaneous whenever both arm CSs cover.
    arm_alpha = float(alpha) / 2.0
    control_cs = _arm_confidence_sequence(control, metric, alpha=arm_alpha)
    candidate_cs = _arm_confidence_sequence(candidate, metric, alpha=arm_alpha)
    if metric.direction == "higher_is_better":
        estimate = candidate_cs.estimate - control_cs.estimate
        lower = candidate_cs.lower - control_cs.upper
        upper = candidate_cs.upper - control_cs.lower
    else:
        estimate = control_cs.estimate - candidate_cs.estimate
        lower = control_cs.lower - candidate_cs.upper
        upper = control_cs.upper - candidate_cs.lower
    return {
        "benefit_estimate": estimate,
        "benefit_lower": lower,
        "benefit_upper": upper,
        "positive_means": "candidate_better",
        "control": control_cs.to_dict(),
        "candidate": candidate_cs.to_dict(),
        "metric_alpha": alpha,
        "familywise_method": "bonferroni_metrics_and_arms",
    }


def _dirichlet_multinomial_log_e(
    counts: tuple[int, int],
    expected: tuple[float, float],
    *,
    prior: float,
) -> float:
    """Mixture likelihood ratio e-value for a two-arm allocation null."""

    n0, n1 = (int(counts[0]), int(counts[1]))
    p0, p1 = (float(expected[0]), float(expected[1]))
    total = n0 + n1
    if total == 0:
        return 0.0
    if p0 <= 0.0 or p1 <= 0.0:
        raise ValueError("SRM expected arm probabilities must be positive")
    a0 = a1 = float(prior)
    log_mixture = (
        lgamma(a0 + a1)
        - lgamma(a0 + a1 + total)
        + lgamma(a0 + n0)
        - lgamma(a0)
        + lgamma(a1 + n1)
        - lgamma(a1)
    )
    log_null = n0 * log(p0) + n1 * log(p1)
    return log_mixture - log_null


def _validate_and_order(
    observations: Iterable[OnlineObservation],
    spec: OnlineExperimentSpec,
    epochs: tuple[AllocationEpoch, ...],
) -> list[OnlineObservation]:
    epoch_map = {epoch.epoch_id: epoch for epoch in epochs}
    if len(epoch_map) != len(epochs):
        raise ValueError("allocation epoch ids must be unique")
    stages = {stage.stage_index: stage for stage in spec.stages}
    for epoch in epochs:
        stage = stages.get(epoch.stage_index)
        if stage is None:
            raise ValueError(f"unknown stage for allocation epoch: {epoch.epoch_id}")
        if abs(float(stage.candidate_fraction) - float(epoch.candidate_fraction)) > 1e-12:
            raise ValueError("allocation epoch fraction does not match ramp stage")

    rows = sorted(list(observations), key=lambda row: int(row.sequence))
    unit_ids: set[str] = set()
    sequences: set[int] = set()
    previous_stage = -1
    metric_map = {metric.name: metric for metric in spec.metrics}
    for row in rows:
        if row.unit_id in unit_ids:
            raise ValueError(f"duplicate randomized unit_id: {row.unit_id}")
        if int(row.sequence) in sequences:
            raise ValueError(f"duplicate assignment sequence: {row.sequence}")
        unit_ids.add(row.unit_id)
        sequences.add(int(row.sequence))
        epoch = epoch_map.get(row.epoch_id)
        if epoch is None:
            raise ValueError(f"unknown allocation epoch: {row.epoch_id}")
        if epoch.stage_index < previous_stage:
            raise ValueError("allocation epochs must not move backward in assignment order")
        previous_stage = epoch.stage_index
        if row.arm not in {spec.control_arm, spec.candidate_arm}:
            raise ValueError(f"unknown experiment arm: {row.arm}")
        unknown_metrics = set(row.metrics) - set(metric_map)
        if unknown_metrics:
            raise ValueError(f"unknown online metrics: {sorted(unknown_metrics)}")
        for name, raw in row.metrics.items():
            value = float(raw)
            if not isfinite(value) or value < 0.0 or value > 1.0:
                raise ValueError(f"metric {name} must be finite within [0, 1]")
            if metric_map[name].kind == "bernoulli" and value not in {0.0, 1.0}:
                raise ValueError(f"bernoulli metric {name} must be exactly 0 or 1")
        for name, raw in row.pre_exposure.items():
            if not name.strip() or not isfinite(float(raw)):
                raise ValueError("pre-exposure covariates must have finite values")
    return rows


def _srm_evidence(
    observations: list[OnlineObservation],
    spec: OnlineExperimentSpec,
    epochs: tuple[AllocationEpoch, ...],
) -> dict[str, Any]:
    epoch_map = {epoch.epoch_id: epoch for epoch in epochs}
    counts = {epoch.epoch_id: [0, 0] for epoch in epochs}
    threshold_log = log(1.0 / float(spec.srm_alpha))
    max_log_e = 0.0
    current_log_e = 0.0
    first_crossing_sequence: int | None = None

    def combined_log_e() -> float:
        total = 0.0
        for epoch in epochs:
            control_count, candidate_count = counts[epoch.epoch_id]
            total += _dirichlet_multinomial_log_e(
                (control_count, candidate_count),
                (1.0 - epoch.candidate_fraction, epoch.candidate_fraction),
                prior=spec.srm_dirichlet_prior,
            )
        return total

    for row in observations:
        arm_index = 0 if row.arm == spec.control_arm else 1
        counts[row.epoch_id][arm_index] += 1
        current_log_e = combined_log_e()
        if current_log_e > max_log_e:
            max_log_e = current_log_e
        if first_crossing_sequence is None and max_log_e >= threshold_log:
            first_crossing_sequence = int(row.sequence)

    per_epoch = []
    for epoch in epochs:
        control_count, candidate_count = counts[epoch.epoch_id]
        log_e = _dirichlet_multinomial_log_e(
            (control_count, candidate_count),
            (1.0 - epoch.candidate_fraction, epoch.candidate_fraction),
            prior=spec.srm_dirichlet_prior,
        )
        per_epoch.append(
            {
                "epoch_id": epoch.epoch_id,
                "stage_index": epoch.stage_index,
                "expected_candidate_fraction": epoch.candidate_fraction,
                "control_count": control_count,
                "candidate_count": candidate_count,
                "log_e_value": log_e,
                "e_value": exp(min(log_e, 700.0)),
            }
        )
    failed = max_log_e >= threshold_log
    return {
        "method": "dirichlet_multinomial_mixture_e_process",
        "alpha": spec.srm_alpha,
        "alarm_threshold": 1.0 / spec.srm_alpha,
        "current_log_e_value": current_log_e,
        "current_e_value": exp(min(current_log_e, 700.0)),
        "max_log_e_value": max_log_e,
        "max_e_value": exp(min(max_log_e, 700.0)),
        "failed_anytime": failed,
        "first_crossing_sequence": first_crossing_sequence,
        "epoch_specific_expected_allocation": True,
        "per_epoch": per_epoch,
    }


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    center = fmean(values)
    return sum((value - center) ** 2 for value in values) / (len(values) - 1)


def _cuped_diagnostic(
    rows: list[OnlineObservation],
    metric: OnlineMetricSpec,
    *,
    control_arm: str,
    candidate_arm: str,
) -> dict[str, Any] | None:
    covariate = metric.cuped_covariate
    if not covariate:
        return None
    control_pairs = [
        (float(row.pre_exposure[covariate]), float(row.metrics[metric.name]))
        for row in rows
        if row.arm == control_arm
        and covariate in row.pre_exposure
        and metric.name in row.metrics
    ]
    if len(control_pairs) < 3:
        return {
            "available": False,
            "reason": "insufficient_control_pre_exposure_pairs",
            "decision_uses_cuped": False,
        }
    x_values = [pair[0] for pair in control_pairs]
    y_values = [pair[1] for pair in control_pairs]
    x_mean = fmean(x_values)
    y_mean = fmean(y_values)
    x_var_sum = sum((value - x_mean) ** 2 for value in x_values)
    if x_var_sum <= 1e-15:
        return {
            "available": False,
            "reason": "zero_control_covariate_variance",
            "decision_uses_cuped": False,
        }
    covariance_sum = sum(
        (x - x_mean) * (y - y_mean) for x, y in control_pairs
    )
    theta = covariance_sum / x_var_sum
    all_pairs = [
        (row.arm, float(row.pre_exposure[covariate]), float(row.metrics[metric.name]))
        for row in rows
        if row.arm in {control_arm, candidate_arm}
        and covariate in row.pre_exposure
        and metric.name in row.metrics
    ]
    raw = [value for _, _, value in all_pairs]
    adjusted = [value - theta * (x - x_mean) for _, x, value in all_pairs]
    raw_variance = _sample_variance(raw)
    adjusted_variance = _sample_variance(adjusted)
    ratio = (
        adjusted_variance / raw_variance
        if raw_variance is not None
        and raw_variance > 1e-15
        and adjusted_variance is not None
        else None
    )
    return {
        "available": True,
        "covariate": covariate,
        "theta_from_control_only": theta,
        "paired_units": len(all_pairs),
        "raw_variance": raw_variance,
        "adjusted_variance": adjusted_variance,
        "variance_ratio": ratio,
        "decision_uses_cuped": False,
        "reason": "diagnostic_only_until_adjusted_support_is_proven",
    }


def evaluate_online_experiment(
    observations: Iterable[OnlineObservation],
    spec: OnlineExperimentSpec,
    *,
    epochs: tuple[AllocationEpoch, ...],
    current_epoch_id: str,
) -> dict[str, Any]:
    """Return an anytime-valid hold/ramp/rollback recommendation.

    This function has no side effect and cannot activate a policy. Callers may use
    ``action`` to orchestrate a separately governed traffic controller.
    """

    ordered = _validate_and_order(observations, spec, epochs)
    epoch_map = {epoch.epoch_id: epoch for epoch in epochs}
    current_epoch = epoch_map.get(current_epoch_id)
    if current_epoch is None:
        raise ValueError("current_epoch_id is not declared")
    if epochs[-1].epoch_id != current_epoch_id:
        raise ValueError("current_epoch_id must be the latest declared allocation epoch")
    stage = spec.stages[current_epoch.stage_index]
    current_rows = [row for row in ordered if row.epoch_id == current_epoch_id]
    srm = _srm_evidence(ordered, spec, epochs)

    metric_alpha = float(spec.outcome_alpha) / len(spec.metrics)
    metric_results: dict[str, Any] = {}
    blockers: list[str] = []
    harmful: list[str] = []
    passed: list[str] = []

    current_control_units = sum(row.arm == spec.control_arm for row in current_rows)
    current_candidate_units = sum(row.arm == spec.candidate_arm for row in current_rows)
    if len(current_rows) < int(stage.minimum_randomized_units):
        blockers.append(
            f"randomized_units<{int(stage.minimum_randomized_units)}"
        )

    for metric in spec.metrics:
        control_values = [
            float(row.metrics[metric.name])
            for row in current_rows
            if row.arm == spec.control_arm and metric.name in row.metrics
        ]
        candidate_values = [
            float(row.metrics[metric.name])
            for row in current_rows
            if row.arm == spec.candidate_arm and metric.name in row.metrics
        ]
        enough = (
            len(control_values) >= int(metric.minimum_samples_per_arm)
            and len(candidate_values) >= int(metric.minimum_samples_per_arm)
        )
        result: dict[str, Any] = {
            "role": metric.role,
            "kind": metric.kind,
            "direction": metric.direction,
            "advance_threshold": metric.advance_threshold,
            "rollback_threshold": metric.rollback_threshold,
            "minimum_samples_per_arm": metric.minimum_samples_per_arm,
            "control_mature_samples": len(control_values),
            "candidate_mature_samples": len(candidate_values),
            "familywise_metric_alpha": metric_alpha,
            "current_epoch_only": True,
            "cuped": _cuped_diagnostic(
                current_rows,
                metric,
                control_arm=spec.control_arm,
                candidate_arm=spec.candidate_arm,
            ),
        }
        if not enough:
            result["status"] = "insufficient_maturity"
            blockers.append(f"{metric.name}:insufficient_maturity")
            metric_results[metric.name] = result
            continue
        effect = _effect_confidence_sequence(
            control_values,
            candidate_values,
            metric,
            alpha=metric_alpha,
        )
        result["effect"] = effect
        if float(effect["benefit_upper"]) <= float(metric.rollback_threshold):
            result["status"] = "confidently_harmful"
            harmful.append(metric.name)
        elif float(effect["benefit_lower"]) >= float(metric.advance_threshold):
            result["status"] = "advance_criterion_met"
            passed.append(metric.name)
        else:
            result["status"] = "inconclusive"
            blockers.append(f"{metric.name}:inconclusive")
        metric_results[metric.name] = result

    if srm["failed_anytime"]:
        harmful.insert(0, "sample_ratio_mismatch")

    if harmful:
        action = "rollback_recommended"
        next_stage_index = None
        blockers = [*dict.fromkeys([*harmful, *blockers])]
    elif len(passed) == len(spec.metrics) and not blockers:
        if current_epoch.stage_index == len(spec.stages) - 1:
            action = "eligible_for_promotion_review"
            next_stage_index = None
        else:
            action = "advance_ramp"
            next_stage_index = current_epoch.stage_index + 1
    else:
        action = "hold"
        next_stage_index = None
        blockers = list(dict.fromkeys(blockers))

    return {
        "experiment_id": spec.experiment_id,
        "current_epoch_id": current_epoch_id,
        "current_stage_index": current_epoch.stage_index,
        "current_candidate_fraction": current_epoch.candidate_fraction,
        "current_randomized_units": len(current_rows),
        "current_control_units": current_control_units,
        "current_candidate_units": current_candidate_units,
        "srm": srm,
        "metrics": metric_results,
        "decision": {
            "action": action,
            "next_stage_index": next_stage_index,
            "automatic_activation": False,
            "activation_authority": "external_promotion_review_only",
            "outcome_inference_epoch_scope": "current_allocation_epoch_only",
            "passed_metrics": passed,
            "harmful_signals": harmful,
            "blockers": blockers,
        },
    }


__all__ = [
    "AllocationEpoch",
    "ConfidenceSequence",
    "OnlineExperimentSpec",
    "OnlineMetricSpec",
    "OnlineObservation",
    "RampStage",
    "bernoulli_mixture_confidence_sequence",
    "bounded_alpha_spending_confidence_sequence",
    "evaluate_online_experiment",
]
