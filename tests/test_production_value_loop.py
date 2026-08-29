from __future__ import annotations

from dataclasses import replace

from lingjing_harness.algorithms import (
    RecommendationEngine,
    SearchEngine,
    audit_recommend,
    evolve_recommend,
)
from lingjing_harness.algorithms.production_evolution import _business_confidence_supports_trust
from lingjing_harness.domain import Catalog
from lingjing_harness.production import (
    ExposureEvent,
    RewardSpec,
    evaluate_logged_policy,
    paired_bootstrap_delta,
    request_groups,
    temporal_request_split,
)
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def _production_catalog(surface: str = "recommend", requests: int = 8) -> Catalog:
    base = build_sample_catalog()
    search = SearchEngine(base)
    recommend = RecommendationEngine(base)
    events: list[ExposureEvent] = []
    users = recommend.known_users()
    labels = base.query_labels
    for index in range(requests):
        request_id = f"req-{surface}-{index:02d}"
        timestamp = float(100 + index)
        if surface == "recommend":
            user_id = users[index % len(users)]
            top = recommend.recommend(user_id, limit=3)[0]["id"]
            events.extend(
                [
                    ExposureEvent(
                        request_id=request_id,
                        timestamp=timestamp,
                        surface="recommend",
                        user_id=user_id,
                        item_id=top,
                        event="impression",
                        position=1,
                        propensity=0.5,
                        policy_id="owned-default",
                    ),
                    ExposureEvent(
                        request_id=request_id,
                        timestamp=timestamp + 0.01,
                        surface="recommend",
                        user_id=user_id,
                        item_id=top,
                        event="click",
                        value=1.0,
                        propensity=0.5,
                        position=1,
                        policy_id="owned-default",
                    ),
                ]
            )
        else:
            label = labels[index % len(labels)]
            ranked = search.search(label.query, limit=3)
            assert ranked
            top = ranked[0]["id"]
            events.extend(
                [
                    ExposureEvent(
                        request_id=request_id,
                        timestamp=timestamp,
                        surface="search",
                        query=label.query,
                        item_id=top,
                        event="impression",
                        position=1,
                        propensity=0.4,
                        policy_id="owned-default",
                    ),
                    ExposureEvent(
                        request_id=request_id,
                        timestamp=timestamp + 0.01,
                        surface="search",
                        query=label.query,
                        item_id=top,
                        event="click",
                        value=1.0,
                        propensity=0.4,
                        position=1,
                        policy_id="owned-default",
                    ),
                ]
            )
    return Catalog(
        items=list(base.items),
        interactions=list(base.interactions),
        query_labels=list(base.query_labels),
        events=events,
        reward_spec=RewardSpec(weights={"impression": 0.0, "click": 1.0, "purchase": 5.0, "hide": -2.0}),
        name=f"production-{surface}",
    )


def test_catalog_roundtrip_preserves_reward_contract_and_exposure_identity():
    catalog = _production_catalog("recommend")
    payload = catalog.to_payload()
    restored = Catalog.from_payload(payload, name="roundtrip")
    assert restored.reward_spec is not None
    assert restored.reward_spec.weights["purchase"] == 5.0
    assert len(restored.events) == len(catalog.events)
    assert restored.events[0].request_id == catalog.events[0].request_id
    assert restored.summary()["business_reward_ready"] is True
    assert restored.summary()["recommend_replay_requests"] == 8


def test_temporal_split_never_splits_request_and_holdout_is_future():
    catalog = _production_catalog("recommend", requests=12)
    discovery, holdout = temporal_request_split(catalog.events, surface="recommend")
    discovery_ids = set(request_groups(discovery, surface="recommend"))
    holdout_ids = set(request_groups(holdout, surface="recommend"))
    assert discovery_ids
    assert len(holdout_ids) >= 2
    assert discovery_ids.isdisjoint(holdout_ids)
    assert max(row.timestamp for row in discovery) < min(row.timestamp for row in holdout)


def test_logged_replay_is_reward_weighted_and_propensity_aware():
    catalog = _production_catalog("recommend")
    engine = RecommendationEngine(catalog)
    report = evaluate_logged_policy(
        catalog.events,
        surface="recommend",
        reward_spec=catalog.reward_spec,
        recommend_engine=engine,
    )
    assert report["requests"] == 8
    assert report["estimator"] == "propensity_weighted_logged_replay"
    assert report["reward"] > 0.9
    assert report["reward_coverage"] == 1.0
    assert report["propensity_rows"] == 16


def test_bootstrap_is_paired_by_request_identity():
    reference = {f"r{i}": 0.2 for i in range(8)}
    candidate = {f"r{i}": 0.4 for i in range(8)}
    result = paired_bootstrap_delta(reference, candidate)
    assert result["available"] is True
    assert result["samples"] == 8
    assert result["delta"] > 0
    assert result["ci95"][0] > 0
    assert result["probability_positive"] == 1.0
    assert _business_confidence_supports_trust(result) is True


def test_singleton_paired_delta_keeps_point_estimate_without_uncertainty():
    result = paired_bootstrap_delta({"r1": 0.2}, {"r1": 0.4})
    assert result["available"] is False
    assert result["samples"] == 1
    assert result["delta"] == 0.2
    assert result["ci95"] is None
    assert result["probability_positive"] is None
    assert "at least two paired requests" in result["reason"]
    assert _business_confidence_supports_trust(result) is False


def test_no_common_requests_do_not_create_paired_confidence():
    result = paired_bootstrap_delta({"reference-only": 0.2}, {"candidate-only": 0.4})
    assert result["available"] is False
    assert result["samples"] == 0
    assert result["delta"] == 0.0
    assert result["ci95"] is None
    assert result["probability_positive"] is None
    assert _business_confidence_supports_trust(result) is False


def test_trust_gate_requires_available_multi_request_confidence():
    assert _business_confidence_supports_trust(
        {"available": True, "samples": 2, "probability_positive": 0.65}
    ) is True
    assert _business_confidence_supports_trust(
        {"available": True, "samples": 1, "probability_positive": 1.0}
    ) is False
    assert _business_confidence_supports_trust(
        {"available": False, "samples": 20, "probability_positive": 1.0}
    ) is False


def test_minimum_business_split_can_have_one_holdout_request_but_not_trust_evidence():
    catalog = _production_catalog("recommend", requests=4)
    _, holdout = temporal_request_split(catalog.events, surface="recommend")
    holdout_ids = set(request_groups(holdout, surface="recommend"))
    assert len(holdout_ids) == 1

    confidence = paired_bootstrap_delta({next(iter(holdout_ids)): 0.1}, {next(iter(holdout_ids)): 0.5})
    assert confidence["delta"] > 0
    assert confidence["available"] is False
    assert _business_confidence_supports_trust(confidence) is False


def test_public_recommend_audit_separates_proxy_quality_from_business_reward():
    catalog = _production_catalog("recommend")
    report = audit_recommend(catalog, RecommendationEngine(catalog))
    assert report["business_reward_available"] is True
    assert report["business_requests"] == 8
    assert report["business_reward"] > 0
    assert report["proxy_quality"] == report["quality"]
    assert "business_reward" in report and "proxy_quality" in report


def test_recommend_evolution_routes_by_business_reward_with_temporal_holdout():
    catalog = _production_catalog("recommend", requests=12)
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    assert result["evaluation_ready"] is True
    assert result["evaluation_basis"] == "business_reward+recommendation_guardrails"
    assert result["business_validation"]["available"] is True
    assert result["business_validation"]["temporal"] is True
    assert result["business_validation"]["holdout_requests"] >= 2
    assert result["business_validation"]["confidence"]["available"] is True
    assert result["business_validation"]["confidence"]["samples"] >= 2
    assert result["evolution"]["business_reward_routed"] is True
    assert "business_reward" in result["delta"]
    assert "business_reward" in result["candidate"]


def test_proxy_only_catalog_is_explicitly_labeled_proxy_not_business_trusted():
    catalog = build_sample_catalog()
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    assert result["evaluation_basis"] == "proxy_metrics"
    assert result["business_trusted"] is False
    assert result["business_validation"]["available"] is False


def test_tool_registry_fork_keeps_production_aware_lifecycle():
    catalog = _production_catalog("recommend")
    registry = ToolRegistry(catalog)
    fork = registry.fork()
    assert type(fork) is type(registry)
    inspected = fork.inspect_data()
    assert inspected["summary"]["business_reward_ready"] is True
    assert not any("未配置业务 RewardSpec" in issue for issue in inspected["issues"])


def test_negative_reward_is_improved_when_bad_item_is_removed_from_slate():
    catalog = _production_catalog("recommend", requests=8)
    engine = RecommendationEngine(catalog)
    user = engine.known_users()[0]
    slate = engine.recommend(user, limit=8)
    bad = slate[-1]["id"]
    event = ExposureEvent(
        request_id="negative-req",
        timestamp=999.0,
        surface="recommend",
        user_id=user,
        item_id=bad,
        event="hide",
        value=1.0,
        propensity=1.0,
        position=8,
    )
    spec = RewardSpec(weights={"hide": -1.0})
    reference = evaluate_logged_policy([event], surface="recommend", reward_spec=spec, recommend_engine=engine)

    class RemoveBad:
        def recommend(self, user_id: str, *, limit: int = 10):
            return [row for row in engine.recommend(user_id, limit=limit + 1) if row["id"] != bad][:limit]

    candidate = evaluate_logged_policy([event], surface="recommend", reward_spec=spec, recommend_engine=RemoveBad())
    assert candidate["reward"] > reference["reward"]
