from __future__ import annotations

from dataclasses import replace
from math import isfinite, log2
import time
from typing import Any, Iterable

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_observation_weighting import (
    describe_weighted_optimizer_landscape,
)

from . import optimizer_routing_epoch as routing_epoch


OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS = 14.0
OPTIMIZER_OBSERVATION_RECENCY_FLOOR = 0.125
OPTIMIZER_OBSERVATION_RECENCY_GRACE_SECONDS = 300.0
OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP = 2.0
OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS = 4.0
OPTIMIZER_OBSERVATION_MAX_WEIGHT_SHARE = 0.50
OPTIMIZER_OBSERVATION_EXIT_MIN_EFFECTIVE_ROWS = 3.5
OPTIMIZER_OBSERVATION_EXIT_MAX_WEIGHT_SHARE = 0.55
_ROUTING_REGIME_ATTR = "_optimizer_observation_routing_regimes"
_ROUTING_REGIME_WEIGHTED = "weighted"
_ROUTING_REGIME_FALLBACK = "fallback"
_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def optimizer_observation_routing_weight(
    observation: dict[str, Any],
    *,
    reference_time: float | None = None,
) -> dict[str, float]:
    """Return bounded routing weights from recency and repeated evidence only."""

    now = _finite_float(reference_time)
    if now is None:
        now = time.time()
    updated_at = _finite_float(observation.get("updated_at"))
    age_seconds = max(0.0, now - updated_at) if updated_at is not None else 0.0
    decay_age_seconds = max(0.0, age_seconds - OPTIMIZER_OBSERVATION_RECENCY_GRACE_SECONDS)
    half_life_seconds = OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS * 24.0 * 60.0 * 60.0
    recency = 0.5 ** (decay_age_seconds / half_life_seconds)
    recency = max(OPTIMIZER_OBSERVATION_RECENCY_FLOOR, min(1.0, recency))

    try:
        seen_count = max(1, int(observation.get("seen_count", 1) or 1))
    except (TypeError, ValueError):
        seen_count = 1
    evidence = 1.0 + 0.25 * log2(float(seen_count))
    evidence = max(1.0, min(OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP, evidence))

    return {
        "routing_weight": recency * evidence,
        "routing_recency_weight": recency,
        "routing_evidence_weight": evidence,
        "routing_age_seconds": age_seconds,
        "routing_decay_age_seconds": decay_age_seconds,
    }


def weight_optimizer_observations(
    observations: Iterable[dict[str, Any]],
    *,
    reference_time: float | None = None,
) -> list[dict[str, Any]]:
    """Attach transient routing weights without mutating durable observation rows."""

    rows = [dict(row) for row in observations if isinstance(row, dict)]
    finite_times = [
        value
        for value in (_finite_float(row.get("updated_at")) for row in rows)
        if value is not None
    ]
    now = _finite_float(reference_time)
    if now is None:
        now = max(time.time(), max(finite_times, default=0.0))
    weighted: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        enriched.update(optimizer_observation_routing_weight(row, reference_time=now))
        weighted.append(enriched)
    return weighted


def optimizer_observation_weight_diagnostics(
    observations: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Measure effective fresh evidence and concentration without evaluator work."""

    rows = [row for row in observations if isinstance(row, dict)]
    weights: list[float] = []
    for row in rows:
        weight = _finite_float(row.get("routing_weight"))
        if weight is None or weight <= 0.0:
            continue
        weights.append(weight)

    if not weights:
        return {
            "raw_rows": len(rows),
            "weighted_rows": 0,
            "total_weight": 0.0,
            "kish_effective_rows": 0.0,
            "effective_rows": 0.0,
            "max_weight_share": 1.0,
            "enter_confident": False,
            "stay_confident": False,
            "confident": False,
            "new_evaluator_calls": 0,
        }

    total_weight = sum(weights)
    squared_weight = sum(weight * weight for weight in weights)
    kish_effective_rows = (
        total_weight * total_weight / squared_weight
        if squared_weight > 0.0
        else 0.0
    )
    effective_rows = min(kish_effective_rows, total_weight)
    max_weight_share = max(weights) / total_weight
    enough_rows = len(weights) >= int(OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS)
    enter_confident = (
        enough_rows
        and effective_rows + 1e-12 >= OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS
        and max_weight_share <= OPTIMIZER_OBSERVATION_MAX_WEIGHT_SHARE + 1e-12
    )
    stay_confident = (
        enough_rows
        and effective_rows + 1e-12 >= OPTIMIZER_OBSERVATION_EXIT_MIN_EFFECTIVE_ROWS
        and max_weight_share <= OPTIMIZER_OBSERVATION_EXIT_MAX_WEIGHT_SHARE + 1e-12
    )
    return {
        "raw_rows": len(rows),
        "weighted_rows": len(weights),
        "total_weight": total_weight,
        "kish_effective_rows": kish_effective_rows,
        "effective_rows": effective_rows,
        "max_weight_share": max_weight_share,
        "enter_confident": enter_confident,
        "stay_confident": stay_confident,
        "confident": enter_confident,
        "new_evaluator_calls": 0,
    }


def optimizer_observation_hysteresis_regime(
    diagnostics: dict[str, Any],
    current_regime: str | None,
) -> str:
    """Choose a routing regime with looser exit than entry confidence."""

    if current_regime == _ROUTING_REGIME_WEIGHTED:
        return (
            _ROUTING_REGIME_WEIGHTED
            if bool(diagnostics.get("stay_confident"))
            else _ROUTING_REGIME_FALLBACK
        )
    return (
        _ROUTING_REGIME_WEIGHTED
        if bool(diagnostics.get("enter_confident", diagnostics.get("confident")))
        else _ROUTING_REGIME_FALLBACK
    )


def _routing_regimes(registry: Any) -> dict[str, str]:
    regimes = getattr(registry, _ROUTING_REGIME_ATTR, None)
    if not isinstance(regimes, dict):
        regimes = {}
        setattr(registry, _ROUTING_REGIME_ATTR, regimes)
    return regimes


def _pre_observation_context(registry: Any, surface: str, context: Any):
    fallback = getattr(
        registry,
        "_routing_context_without_optimizer_observations",
        None,
    )
    return fallback(surface) if callable(fallback) else context


def install_optimizer_observation_weighting(optimizer_registry_cls: type) -> None:
    """Apply transient weighted geometry with per-registry routing hysteresis."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_weighted_durable_geometry(self: Any, surface: str):
        context = original_routing_context(self, surface)
        regimes = _routing_regimes(self)
        reader = getattr(self.memory, "optimizer_observations", None)
        if not callable(reader):
            regimes[surface] = _ROUTING_REGIME_FALLBACK
            return context
        observations = routing_epoch.filter_routing_epoch_rows(
            reader(self.catalog_key, surface),
            timestamp_key="updated_at",
            epoch_started_at=routing_epoch.routing_epoch_boundary(self, surface),
        )
        if len(observations) < int(OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS):
            regimes[surface] = _ROUTING_REGIME_FALLBACK
            return _pre_observation_context(self, surface, context)

        weighted = weight_optimizer_observations(observations)
        diagnostics = optimizer_observation_weight_diagnostics(weighted)
        next_regime = optimizer_observation_hysteresis_regime(
            diagnostics,
            regimes.get(surface),
        )
        regimes[surface] = next_regime
        if next_regime != _ROUTING_REGIME_WEIGHTED:
            return _pre_observation_context(self, surface, context)

        engine = self.search if surface == "search" else self.recommend
        try:
            dimensions, _ = core._evolution_schema(engine.config)
            landscape = describe_weighted_optimizer_landscape(
                dimensions=dimensions,
                observations=weighted,
            )
        except (TypeError, ValueError, KeyError):
            regimes[surface] = _ROUTING_REGIME_FALLBACK
            return _pre_observation_context(self, surface, context)
        if not landscape.informative:
            regimes[surface] = _ROUTING_REGIME_FALLBACK
            return _pre_observation_context(self, surface, context)
        return replace(context, landscape=landscape)

    optimizer_registry_cls._routing_context = routing_context_with_weighted_durable_geometry

    original_fork = optimizer_registry_cls.fork

    def fork_with_optimizer_observation_regime(self: Any):
        clone = original_fork(self)
        regimes = getattr(self, _ROUTING_REGIME_ATTR, None)
        if isinstance(regimes, dict):
            setattr(clone, _ROUTING_REGIME_ATTR, dict(regimes))
        return clone

    optimizer_registry_cls.fork = fork_with_optimizer_observation_regime

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_observation_weighting(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        regimes = getattr(self, _ROUTING_REGIME_ATTR, None)
        router.update(
            {
                "optimizer_observation_weighting": "recency_half_life_and_log_evidence",
                "optimizer_observation_recency_half_life_days": OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS,
                "optimizer_observation_recency_floor": OPTIMIZER_OBSERVATION_RECENCY_FLOOR,
                "optimizer_observation_recency_grace_seconds": OPTIMIZER_OBSERVATION_RECENCY_GRACE_SECONDS,
                "optimizer_observation_evidence_weight_cap": OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP,
                "optimizer_observation_confidence": "effective_fresh_rows_and_weight_concentration",
                "optimizer_observation_effective_rows_method": "min_kish_ess_and_total_routing_weight",
                "optimizer_observation_min_effective_rows": OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS,
                "optimizer_observation_max_weight_share": OPTIMIZER_OBSERVATION_MAX_WEIGHT_SHARE,
                "optimizer_observation_confidence_fallback": "pre_observation_router",
                "optimizer_observation_hysteresis": "per_registry_routing_regime",
                "optimizer_observation_exit_min_effective_rows": OPTIMIZER_OBSERVATION_EXIT_MIN_EFFECTIVE_ROWS,
                "optimizer_observation_exit_max_weight_share": OPTIMIZER_OBSERVATION_EXIT_MAX_WEIGHT_SHARE,
                "optimizer_observation_routing_regimes": dict(regimes) if isinstance(regimes, dict) else {},
                "optimizer_observation_weighting_authority": "routing_descriptor_only",
                "optimizer_observation_weighting_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_observation_weighting
    _INSTALLED = True


__all__ = [
    "OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP",
    "OPTIMIZER_OBSERVATION_EXIT_MAX_WEIGHT_SHARE",
    "OPTIMIZER_OBSERVATION_EXIT_MIN_EFFECTIVE_ROWS",
    "OPTIMIZER_OBSERVATION_MAX_WEIGHT_SHARE",
    "OPTIMIZER_OBSERVATION_MIN_EFFECTIVE_ROWS",
    "OPTIMIZER_OBSERVATION_RECENCY_FLOOR",
    "OPTIMIZER_OBSERVATION_RECENCY_GRACE_SECONDS",
    "OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS",
    "install_optimizer_observation_weighting",
    "optimizer_observation_hysteresis_regime",
    "optimizer_observation_routing_weight",
    "optimizer_observation_weight_diagnostics",
    "weight_optimizer_observations",
]
