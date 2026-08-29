from __future__ import annotations

from typing import Any, Iterable

from lingjing_harness.production import ExposureEvent, request_groups

from . import production_evolution as _production_evolution
from . import segment_evolution as _segment_evolution


MAX_BUSINESS_OPTIMIZER_REQUESTS = 64
_ORIGINAL_TEMPORAL_REQUEST_SPLIT = _production_evolution.temporal_request_split
_ORIGINAL_SEARCH_SEGMENT_ENTRY = _segment_evolution._search_entry
_ORIGINAL_RECOMMEND_SEGMENT_ENTRY = _segment_evolution._recommend_entry
_INSTALLED = False


def limit_business_optimizer_events(
    events: Iterable[ExposureEvent],
    *,
    surface: str,
    limit: int = MAX_BUSINESS_OPTIMIZER_REQUESTS,
) -> list[ExposureEvent]:
    """Keep the most recent complete request identities from one optimizer slice."""

    rows = list(events)
    grouped = request_groups(rows, surface=surface)
    limit = max(1, int(limit))
    if len(grouped) <= limit:
        return rows
    ranked = sorted(
        grouped.items(),
        key=lambda item: (
            max(row.timestamp for row in item[1]),
            item[0],
        ),
    )
    selected = {request_id for request_id, _ in ranked[-limit:]}
    return [row for row in rows if row.request_id in selected]


def bounded_temporal_request_split(
    events: Iterable[ExposureEvent],
    *,
    surface: str,
    holdout_fraction: float = 0.25,
    minimum_requests: int = 4,
) -> tuple[list[ExposureEvent], list[ExposureEvent]]:
    """Bound global optimizer discovery while preserving the complete future holdout."""

    discovery, holdout = _ORIGINAL_TEMPORAL_REQUEST_SPLIT(
        events,
        surface=surface,
        holdout_fraction=holdout_fraction,
        minimum_requests=minimum_requests,
    )
    return (
        limit_business_optimizer_events(discovery, surface=surface),
        holdout,
    )


def _bounded_search_segment_entry(**kwargs: Any) -> dict[str, Any]:
    payload = dict(kwargs)
    payload["discovery_events"] = limit_business_optimizer_events(
        payload.get("discovery_events") or [],
        surface="search",
    )
    return _ORIGINAL_SEARCH_SEGMENT_ENTRY(**payload)


def _bounded_recommend_segment_entry(**kwargs: Any) -> dict[str, Any]:
    payload = dict(kwargs)
    payload["discovery_events"] = limit_business_optimizer_events(
        payload.get("discovery_events") or [],
        surface="recommend",
    )
    return _ORIGINAL_RECOMMEND_SEGMENT_ENTRY(**payload)


def install_business_replay_budget() -> None:
    """Install bounded global and per-segment discovery replay once.

    Candidate search may replay discovery requests many times. Global evolution
    receives at most ``MAX_BUSINESS_OPTIMIZER_REQUESTS`` recent pre-holdout
    requests, and each segment optimizer receives the same independent per-segment
    budget after routing. Complete future holdouts and final full-log validation
    remain owned by their original evaluation paths.
    """

    global _INSTALLED
    if _INSTALLED:
        return
    _production_evolution.temporal_request_split = bounded_temporal_request_split
    _segment_evolution._search_entry = _bounded_search_segment_entry
    _segment_evolution._recommend_entry = _bounded_recommend_segment_entry
    _INSTALLED = True


__all__ = [
    "MAX_BUSINESS_OPTIMIZER_REQUESTS",
    "bounded_temporal_request_split",
    "install_business_replay_budget",
    "limit_business_optimizer_events",
]
