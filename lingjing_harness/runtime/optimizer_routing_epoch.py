from __future__ import annotations

from hashlib import blake2b
import json
from math import isfinite
from typing import Any, Iterable, Mapping


_ROUTING_EPOCH_STATE_ATTR = "_optimizer_observation_routing_epoch_states"
_ROUTING_EPOCH_PENDING_ATTR = "_optimizer_observation_routing_epoch_pending"


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


__all__ = [
    "clear_pending_routing_epoch_advance",
    "filter_routing_epoch_rows",
    "localize_routing_epoch_seen_counts",
    "pending_routing_epoch_advance",
    "request_routing_epoch_advance",
    "routing_epoch_boundary",
    "routing_epoch_state",
    "routing_epoch_states",
    "set_routing_epoch_state",
]
