from __future__ import annotations

from contextvars import ContextVar
from hashlib import blake2b
import json
from math import isfinite
from typing import Any, Iterable, Mapping


_ROUTING_EPOCH_STATE_ATTR = "_optimizer_observation_routing_epoch_states"
_ROUTING_EPOCH_PENDING_ATTR = "_optimizer_observation_routing_epoch_pending"
_ROUTING_EPOCH_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "optimizer_routing_epoch_context",
    default=None,
)
_COUNTS_INSTALLED = False


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _config_key(config: Mapping[str, Any]) -> str | None:
    try:
        raw = json.dumps(
            dict(config),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def routing_epoch_states(registry: Any) -> dict[str, dict[str, Any]]:
    states = getattr(registry, _ROUTING_EPOCH_STATE_ATTR, None)
    if not isinstance(states, dict):
        states = {}
        setattr(registry, _ROUTING_EPOCH_STATE_ATTR, states)
    return states


def routing_epoch_state(registry: Any, surface: str) -> dict[str, Any]:
    state = routing_epoch_states(registry).get(str(surface))
    return dict(state) if isinstance(state, dict) else {
        "evidence_epoch": 0,
        "epoch_started_at": 0.0,
    }


def set_routing_epoch_state(
    registry: Any,
    surface: str,
    *,
    evidence_epoch: int,
    epoch_started_at: float,
) -> dict[str, Any]:
    if isinstance(evidence_epoch, bool):
        raise ValueError("optimizer routing evidence_epoch must be an integer")
    evidence_epoch = int(evidence_epoch)
    epoch_started_at = float(epoch_started_at)
    if evidence_epoch < 0:
        raise ValueError("optimizer routing evidence_epoch must be >= 0")
    if not isfinite(epoch_started_at) or epoch_started_at < 0.0:
        raise ValueError("optimizer routing epoch_started_at must be finite and >= 0")
    state = {
        "evidence_epoch": evidence_epoch,
        "epoch_started_at": epoch_started_at,
    }
    routing_epoch_states(registry)[str(surface)] = state
    return dict(state)


def routing_epoch_boundary(registry: Any, surface: str) -> float:
    state = routing_epoch_state(registry, surface)
    return max(0.0, _finite_float(state.get("epoch_started_at")) or 0.0)


def filter_routing_epoch_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    timestamp_key: str,
    epoch_started_at: float,
) -> list[dict[str, Any]]:
    boundary = max(0.0, _finite_float(epoch_started_at) or 0.0)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if boundary > 0.0:
            timestamp = _finite_float(row.get(timestamp_key))
            if timestamp is None or timestamp + 1e-12 < boundary:
                continue
        filtered.append(dict(row))
    return filtered


def localize_routing_epoch_seen_counts(
    observations: Iterable[Mapping[str, Any]],
    observation_history: Iterable[Mapping[str, Any]],
    *,
    epoch_started_at: float,
) -> list[dict[str, Any]]:
    """Replace cumulative seen_count with bounded epoch-local paid evidence counts.

    Before the first confirmed epoch boundary the durable latest-row seen_count keeps
    its existing lifetime semantics. After a boundary, routing must not inherit
    repeated-evidence credit from a prior regime, so counts are reconstructed only
    from evaluator-paid history rows inside the current epoch. History retention can
    only make this count conservative; it cannot create extra routing authority.
    """

    rows = [dict(row) for row in observations if isinstance(row, Mapping)]
    boundary = max(0.0, _finite_float(epoch_started_at) or 0.0)
    if boundary <= 0.0:
        return rows

    counts: dict[str, int] = {}
    for row in observation_history:
        if not isinstance(row, Mapping):
            continue
        observed_at = _finite_float(row.get("observed_at"))
        if observed_at is None or observed_at + 1e-12 < boundary:
            continue
        config_key = str(row.get("config_key") or "").strip()
        if not config_key:
            config = row.get("config")
            if not isinstance(config, Mapping):
                continue
            config_key = _config_key(config) or ""
        if config_key:
            counts[config_key] = counts.get(config_key, 0) + 1

    localized: list[dict[str, Any]] = []
    for row in rows:
        enriched = dict(row)
        config = row.get("config")
        config_key = _config_key(config) if isinstance(config, Mapping) else None
        enriched["seen_count"] = max(1, counts.get(config_key or "", 0))
        enriched["routing_epoch_seen_count"] = enriched["seen_count"]
        localized.append(enriched)
    return localized


def _pending_advances(registry: Any) -> dict[str, float]:
    pending = getattr(registry, _ROUTING_EPOCH_PENDING_ATTR, None)
    if not isinstance(pending, dict):
        pending = {}
        setattr(registry, _ROUTING_EPOCH_PENDING_ATTR, pending)
    return pending


def request_routing_epoch_advance(
    registry: Any,
    surface: str,
    epoch_started_at: float,
) -> float | None:
    boundary = _finite_float(epoch_started_at)
    if boundary is None or boundary <= routing_epoch_boundary(registry, surface) + 1e-12:
        return None
    pending = _pending_advances(registry)
    current = _finite_float(pending.get(str(surface))) or 0.0
    pending[str(surface)] = max(current, boundary)
    return pending[str(surface)]


def pending_routing_epoch_advance(registry: Any, surface: str) -> float | None:
    return _finite_float(_pending_advances(registry).get(str(surface)))


def clear_pending_routing_epoch_advance(registry: Any, surface: str) -> None:
    _pending_advances(registry).pop(str(surface), None)


def _effective_epoch_boundary(registry: Any, surface: str) -> float:
    pending = pending_routing_epoch_advance(registry, surface)
    if pending is not None:
        return max(routing_epoch_boundary(registry, surface), float(pending))
    return routing_epoch_boundary(registry, surface)


def install_optimizer_routing_epoch_counts(
    agent_memory_cls: type,
    optimizer_registry_cls: type,
) -> None:
    """Localize routing-only repeated evidence without changing durable ledger rows.

    The public latest-observation reader keeps lifetime ``seen_count`` semantics for
    callers outside routing. During one registry routing decision a ContextVar scopes
    a transient reader view to the current (or pending) epoch, so weighting and the
    durable checkpoint version both consume only paid evidence from that regime.
    A detected change point also re-localizes the detector's recent cohort to the
    candidate boundary before entry confidence is evaluated.
    """

    global _COUNTS_INSTALLED
    if _COUNTS_INSTALLED:
        return

    from . import optimizer_observation_drift as drift

    original_observations = agent_memory_cls.optimizer_observations
    original_history = agent_memory_cls.optimizer_observation_history

    def optimizer_observations_with_epoch_counts(
        self: Any,
        catalog_key: str,
        domain: str,
        *args: Any,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        observations = original_observations(
            self,
            catalog_key,
            domain,
            *args,
            **kwargs,
        )
        context = _ROUTING_EPOCH_CONTEXT.get()
        if not isinstance(context, dict):
            return observations
        registry = context.get("registry")
        surface = str(context.get("surface") or "")
        if registry is None or str(domain) != surface:
            return observations
        boundary = _effective_epoch_boundary(registry, surface)
        if boundary <= 0.0:
            return observations

        cache = context.setdefault("history_cache", {})
        cache_key = (str(catalog_key), str(domain), float(boundary))
        history = cache.get(cache_key)
        if history is None:
            history = original_history(self, catalog_key, domain)
            cache[cache_key] = history
        return localize_routing_epoch_seen_counts(
            observations,
            history,
            epoch_started_at=boundary,
        )

    agent_memory_cls.optimizer_observations = optimizer_observations_with_epoch_counts

    original_detect = drift.detect_optimizer_observation_drift

    def detect_with_epoch_local_recent(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_detect(*args, **kwargs)
        context = _ROUTING_EPOCH_CONTEXT.get()
        if not isinstance(context, dict) or not result.get("change_detected"):
            return result
        boundary = _finite_float(result.get("recent_oldest_at"))
        recent = result.get("_recent_observations")
        history = kwargs.get("observation_history")
        if boundary is None or boundary <= 0.0 or not isinstance(recent, list):
            return result
        localized = localize_routing_epoch_seen_counts(
            recent,
            history or [],
            epoch_started_at=boundary,
        )
        enriched = dict(result)
        enriched["_recent_observations"] = localized
        enriched["routing_epoch_recent_seen_count"] = sum(
            max(1, int(row.get("seen_count", 1) or 1))
            for row in localized
        )
        return enriched

    drift.detect_optimizer_observation_drift = detect_with_epoch_local_recent

    original_routing_context = optimizer_registry_cls._routing_context

    def routing_context_with_epoch_count_scope(self: Any, surface: str):
        token = _ROUTING_EPOCH_CONTEXT.set(
            {
                "registry": self,
                "surface": str(surface),
                "history_cache": {},
            }
        )
        try:
            return original_routing_context(self, surface)
        finally:
            _ROUTING_EPOCH_CONTEXT.reset(token)

    optimizer_registry_cls._routing_context = routing_context_with_epoch_count_scope

    original_inspect = optimizer_registry_cls.inspect_data

    def inspect_data_with_epoch_counts(self: Any) -> dict[str, Any]:
        result = original_inspect(self)
        router = dict(result.get("optimizer_meta_router") or {})
        router.update(
            {
                "optimizer_observation_routing_epoch_seen_count": "bounded_paid_history_since_epoch_boundary",
                "optimizer_observation_routing_epoch_seen_count_scope": "routing_context_only",
                "optimizer_observation_routing_epoch_seen_count_authority": "routing_descriptor_only",
                "optimizer_observation_routing_epoch_seen_count_evaluator_calls": 0,
            }
        )
        result["optimizer_meta_router"] = router
        return result

    optimizer_registry_cls.inspect_data = inspect_data_with_epoch_counts
    _COUNTS_INSTALLED = True


__all__ = [
    "clear_pending_routing_epoch_advance",
    "filter_routing_epoch_rows",
    "install_optimizer_routing_epoch_counts",
    "localize_routing_epoch_seen_counts",
    "pending_routing_epoch_advance",
    "request_routing_epoch_advance",
    "routing_epoch_boundary",
    "routing_epoch_state",
    "routing_epoch_states",
    "set_routing_epoch_state",
]
