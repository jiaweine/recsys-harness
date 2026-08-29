from __future__ import annotations

from lingjing_harness.algorithms import RecommendationEngine, evolve_recommend
from lingjing_harness.algorithms.business_replay_budget import (
    MAX_BUSINESS_OPTIMIZER_REQUESTS,
    bounded_temporal_request_split,
)
from lingjing_harness.domain import Catalog
from lingjing_harness.production import (
    ExposureEvent,
    RewardSpec,
    request_groups,
    temporal_request_split,
)
from lingjing_harness.sample_data import build_sample_catalog


def _recommend_production_catalog(requests: int) -> Catalog:
    base = build_sample_catalog()
    engine = RecommendationEngine(base)
    users = engine.known_users()
    events: list[ExposureEvent] = []
    for index in range(requests):
        user_id = users[index % len(users)]
        item_id = engine.recommend(user_id, limit=3)[0]["id"]
        events.append(
            ExposureEvent(
                request_id=f"req-{index:04d}",
                timestamp=1000.0 + index,
                surface="recommend",
                user_id=user_id,
                item_id=item_id,
                event="click",
                value=1.0,
                propensity=0.5,
                position=1,
                policy_id="owned-default",
            )
        )
    return Catalog(
        items=list(base.items),
        interactions=list(base.interactions),
        query_labels=list(base.query_labels),
        events=events,
        reward_spec=RewardSpec(weights={"click": 1.0}),
        name=f"bounded-business-replay-{requests}",
    )


def _request_ids(events: list[ExposureEvent]) -> list[str]:
    grouped = request_groups(events, surface="recommend")
    return [
        request_id
        for request_id, _ in sorted(
            grouped.items(),
            key=lambda item: (max(row.timestamp for row in item[1]), item[0]),
        )
    ]


def test_optimizer_discovery_budget_keeps_recent_complete_requests_and_full_holdout() -> None:
    catalog = _recommend_production_catalog(400)
    original_discovery, original_holdout = temporal_request_split(
        catalog.events,
        surface="recommend",
    )
    bounded_discovery, bounded_holdout = bounded_temporal_request_split(
        catalog.events,
        surface="recommend",
    )

    original_discovery_ids = _request_ids(original_discovery)
    bounded_discovery_ids = _request_ids(bounded_discovery)
    assert len(original_discovery_ids) == 300
    assert len(bounded_discovery_ids) == MAX_BUSINESS_OPTIMIZER_REQUESTS == 64
    assert bounded_discovery_ids == original_discovery_ids[-MAX_BUSINESS_OPTIMIZER_REQUESTS:]

    assert _request_ids(bounded_holdout) == _request_ids(original_holdout)
    assert len(_request_ids(bounded_holdout)) == 100
    assert set(bounded_discovery_ids).isdisjoint(_request_ids(bounded_holdout))
    assert max(row.timestamp for row in bounded_discovery) < min(
        row.timestamp for row in bounded_holdout
    )


def test_public_recommend_evolution_caps_only_discovery_replay() -> None:
    catalog = _recommend_production_catalog(88)
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    business = result["business_validation"]

    assert business["available"] is True
    assert business["temporal"] is True
    assert business["discovery_requests"] == MAX_BUSINESS_OPTIMIZER_REQUESTS == 64
    assert business["holdout_requests"] == 22
    assert business["confidence"]["samples"] == 22

    # Promotion and trust still evaluate the selected candidate and reference on
    # the complete production log; only repeated optimizer discovery is bounded.
    assert result["candidate"]["business_requests"] == 88
    assert result["reference"]["business_requests"] == 88
