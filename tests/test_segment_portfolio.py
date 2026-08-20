from __future__ import annotations

from dataclasses import asdict
import time

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    SearchConfig,
    SearchEngine,
    SegmentRouter,
    evolve_search,
    strategy_domain,
)
from lingjing_harness.domain import Catalog
from lingjing_harness.production import ExposureEvent, RewardSpec, request_groups
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def _portfolio_catalog(*, search_requests: int = 24, recommend_requests: int = 18) -> Catalog:
    base = build_sample_catalog()
    search = SearchEngine(base)
    recommend = RecommendationEngine(base)
    events: list[ExposureEvent] = []
    labels = list(base.query_labels)
    users = recommend.known_users()

    for index in range(search_requests):
        label = labels[index % len(labels)]
        ranked = search.search(label.query, limit=3)
        assert ranked
        item_id = ranked[0]["id"]
        events.extend(
            [
                ExposureEvent(
                    request_id=f"search-request-{index:03d}",
                    timestamp=float(100 + index),
                    surface="search",
                    query=label.query,
                    item_id=item_id,
                    event="impression",
                    position=1,
                    propensity=0.5,
                    policy_id="owned-default",
                ),
                ExposureEvent(
                    request_id=f"search-request-{index:03d}",
                    timestamp=float(100 + index) + 0.01,
                    surface="search",
                    query=label.query,
                    item_id=item_id,
                    event="click",
                    position=1,
                    propensity=0.5,
                    policy_id="owned-default",
                ),
            ]
        )

    for index in range(recommend_requests):
        user_id = users[index % len(users)] if index % 6 else f"new-user-{index:03d}"
        ranked = recommend.recommend(user_id, limit=3)
        assert ranked
        item_id = ranked[0]["id"]
        events.extend(
            [
                ExposureEvent(
                    request_id=f"recommend-request-{index:03d}",
                    timestamp=float(500 + index),
                    surface="recommend",
                    user_id=user_id,
                    item_id=item_id,
                    event="impression",
                    position=1,
                    propensity=0.5,
                    policy_id="owned-default",
                ),
                ExposureEvent(
                    request_id=f"recommend-request-{index:03d}",
                    timestamp=float(500 + index) + 0.01,
                    surface="recommend",
                    user_id=user_id,
                    item_id=item_id,
                    event="click",
                    position=1,
                    propensity=0.5,
                    policy_id="owned-default",
                ),
            ]
        )

    return Catalog(
        items=list(base.items),
        interactions=list(base.interactions),
        query_labels=list(base.query_labels),
        events=events,
        reward_spec=RewardSpec(weights={"impression": 0.0, "click": 1.0, "hide": -2.0}),
        name="segment-portfolio-fixture",
    )


def test_segment_router_uses_traffic_quantiles_and_preserves_request_identity():
    catalog = _portfolio_catalog()
    router = SegmentRouter(catalog, SearchEngine(catalog), RecommendationEngine(catalog))
    manifest = router.manifest("search")
    assert manifest["routing_basis"] == "production_traffic_quantiles"
    assert manifest["contexts"] == 24
    assert set(manifest["thresholds"]) >= {"candidate_low", "anchor_low", "anchor_high"}

    partitions = router.partition_events(catalog.events, surface="search")
    all_request_ids: list[str] = []
    for rows in partitions.values():
        all_request_ids.extend(request_groups(rows, surface="search"))
    assert len(all_request_ids) == 24
    assert len(set(all_request_ids)) == 24


def test_unknown_recommend_user_routes_to_cold_start_without_a_numeric_cutoff():
    catalog = _portfolio_catalog()
    router = SegmentRouter(catalog, SearchEngine(catalog), RecommendationEngine(catalog))
    assert router.recommend_segment("never-observed-user") == "recommend/cold-start"
    assert router.recommend_features("never-observed-user").history_events == 0


def test_segment_portfolio_never_trusts_without_future_and_guardrail_evidence():
    catalog = _portfolio_catalog(search_requests=24, recommend_requests=0)
    result = evolve_search(catalog, SearchEngine(catalog))
    portfolio = result["segment_portfolio"]
    assert portfolio["available"] is True
    assert portfolio["candidate_basis"] == "typed_local_neighborhood_of_global_basin"
    assert portfolio["routing"]["routing_basis"] == "production_traffic_quantiles"
    assert portfolio["entries"]
    for entry in portfolio["entries"]:
        if entry["trusted"]:
            assert entry["discovery_requests"] >= 3
            assert entry["holdout_requests"] >= 2
            assert entry["guardrail"]["available"] is True
            assert entry["confidence"]["samples"] >= 2
            assert entry["holdout_reward_delta"] >= -0.003


def test_active_segment_strategy_routes_only_its_segment_and_survives_fork():
    catalog = _portfolio_catalog()
    memory = AgentMemory()
    initial = ToolRegistry(catalog, memory=memory)
    query = "运动耳机"
    segment = initial.segment_router.search_segment(query)
    config = asdict(SearchConfig())
    config["diversity"] = 0.11
    domain = strategy_domain("search", segment)
    memory.remember_strategy(
        initial.catalog_key,
        domain,
        config,
        score=0.8,
        evidence=8,
        status="active",
        payload={
            "validated_at": time.time(),
            "validation": {"segment": segment, "requests": 8},
        },
    )

    registry = ToolRegistry(catalog, memory=memory)
    routed = registry.run_search(query)
    assert routed["segment"] == segment
    assert routed["strategy_scope"] == "segment"
    assert segment in registry.inspect_data()["active"]["search_portfolio"]

    fork = registry.fork()
    forked = fork.run_search(query)
    assert forked["segment"] == segment
    assert forked["strategy_scope"] == "segment"


def test_partial_production_log_cannot_activate_a_strategy_even_when_requested():
    catalog = _portfolio_catalog(search_requests=6, recommend_requests=0)
    registry = ToolRegistry(catalog)
    result = registry.search_evolve(activate=True)
    assert result["activation_blocked_by"] == "production_request_floor<8"
    assert result["activated"] is False
    assert result["portfolio_activated"] is False


def test_invalid_persisted_segment_config_is_retired_and_global_fallback_runs():
    catalog = _portfolio_catalog()
    memory = AgentMemory()
    initial = ToolRegistry(catalog, memory=memory)
    query = "露营灯"
    segment = initial.segment_router.search_segment(query)
    domain = strategy_domain("search", segment)
    memory.remember_strategy(
        initial.catalog_key,
        domain,
        {"query_strategy": "removed-capability"},
        score=1.0,
        evidence=12,
        status="active",
        payload={"validated_at": time.time()},
    )

    registry = ToolRegistry(catalog, memory=memory)
    routed = registry.run_search(query)
    assert routed["strategy_scope"] == "global"
    assert memory.active_config(registry.catalog_key, domain) is None
    assert any(event["domain"] == domain for event in registry.rollback_events)
