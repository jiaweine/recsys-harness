from __future__ import annotations

from typing import Iterable

from lingjing_harness.production import ExposureEvent, request_groups

from . import production_evolution as _production_evolution


MAX_BUSINESS_OPTIMIZER_REQUESTS = 64
_ORIGINAL_TEMPORAL_REQUEST_SPLIT = _production_evolution.temporal_request_split
_INSTALLED = False


def _recent_request_events(
    events: Iterable[ExposureEvent],
    *,
    surface: str,
    limit: int = MAX_BUSINESS_OPTIMIZER_REQUESTS,
) -> list[ExposureEvent]:
    """Keep the most recent complete request identities from one discovery slice."""

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
    """Bound optimizer discovery replay while preserving the complete future holdout.

    Production evolution evaluates many candidate configurations against discovery
    requests. Replaying every historical request for every candidate makes search
    cost grow with the entire production log. The final full-policy replay and
    future holdout validation remain untouched elsewhere in production_evolution;
    only the repeated optimizer discovery slice is capped here.
    """

    discovery, holdout = _ORIGINAL_TEMPORAL_REQUEST_SPLIT(
        events,
        surface=surface,
        holdout_fraction=holdout_fraction,
        minimum_requests=minimum_requests,
    )
    return (
        _recent_request_events(discovery, surface=surface),
        holdout,
    )


def install_business_replay_budget() -> None:
    """Install the bounded discovery split once at the stable evolution boundary."""

    global _INSTALLED
    if _INSTALLED:
        return
    _production_evolution.temporal_request_split = bounded_temporal_request_split
    _INSTALLED = True


__all__ = [
    "MAX_BUSINESS_OPTIMIZER_REQUESTS",
    "bounded_temporal_request_split",
    "install_business_replay_budget",
]
