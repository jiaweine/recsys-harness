from __future__ import annotations

from dataclasses import replace
from math import isfinite, log2
import time
from typing import Any, Iterable

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_observation_weighting import (
    describe_weighted_optimizer_landscape,
)


OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS = 14.0
OPTIMIZER_OBSERVATION_RECENCY_FLOOR = 0.125
OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP = 2.0
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
    half_life_seconds = OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS * 24.0 * 60.0 * 60.0
    recency = 0.5 ** (age_seconds / half_life_seconds)
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


def install_optimizer_observation_weighting(optimizer_registry_cls: type) -> None:
    """Apply transient recency/evidence weighting to durable router geometry."""

    global _INSTALLED
    if _INSTALLED:
        return

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_weighted_durable_geometry(self: Any, surface: str):
        context = original_routing_context(self, surface)
        reader = getattr(self.memory, "optimizer_observations", None)
        if not callable(reader):
            return context
        observations = reader(self.catalog_key, surface)
        if len(observations) < 4:
            return context
        engine = self.search if surface == "search" else self.recommend
        try:
            dimensions, _ = core._evolution_schema(engine.config)
            landscape = describe_weighted_optimizer_landscape(
                dimensions=dimensions,
                observations=weight_optimizer_observations(observations),
            )
        except (TypeError, ValueError, KeyError):
            return context
        if not landscape.informative:
            return context
        return replace(context, landscape=landscape)

    optimizer_registry_cls._routing_context = routing_context_with_weighted_durable_geometry

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_observation_weighting(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        router.update(
            {
                "optimizer_observation_weighting": "recency_half_life_and_log_evidence",
                "optimizer_observation_recency_half_life_days": OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS,
                "optimizer_observation_recency_floor": OPTIMIZER_OBSERVATION_RECENCY_FLOOR,
                "optimizer_observation_evidence_weight_cap": OPTIMIZER_OBSERVATION_EVIDENCE_WEIGHT_CAP,
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
    "OPTIMIZER_OBSERVATION_RECENCY_FLOOR",
    "OPTIMIZER_OBSERVATION_RECENCY_HALF_LIFE_DAYS",
    "install_optimizer_observation_weighting",
    "optimizer_observation_routing_weight",
    "weight_optimizer_observations",
]
