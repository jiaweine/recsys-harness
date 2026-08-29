from __future__ import annotations

from dataclasses import asdict
import time

import pytest

pytest.importorskip("implicit")

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    audit_recommend,
    audit_recommend_relevance,
    evolve_recommend,
)
from lingjing_harness.algorithms.business_replay_budget import MAX_BUSINESS_OPTIMIZER_REQUESTS
from lingjing_harness.domain import Catalog, Interaction
from lingjing_harness.integrations import (
    ImplicitHybridRecommendationEngine,
    ImplicitRecommendationAdapter,
)
from lingjing_harness.production import ExposureEvent, RewardSpec
from lingjing_harness.runtime import RecommendationBackendToolRegistry
from lingjing_harness.runtime.memory import AgentMemory, catalog_fingerprint
from lingjing_harness.sample_data import build_sample_catalog


def _catalog():
    catalog = build_sample_catalog()
    bridges = {
        "u-bridge-a": ["p01", "p05", "p10", "p13"],
        "u-bridge-b": ["p19", "p05", "p11", "p14"],
        "u-bridge-c": ["p08", "p06", "p12", "p15"],
    }
    for user_id, item_ids in bridges.items():
        for offset, item_id in enumerate(item_ids, start=1):
            catalog.interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=item_id,
                    event="click",
                    weight=1.0,
                    timestamp=float(100 + offset),
                )
            )
    return catalog


def _production_catalog(requests: int = 88) -> Catalog:
    base = _catalog()
    reference = RecommendationEngine(base)
    users = reference.known_users()
    events: list[ExposureEvent] = []
    for index in range(requests):
        user_id = users[index % len(users)]
        item_id = reference.recommend(user_id, limit=3)[0]["id"]
        events.append(
            ExposureEvent(
                request_id=f"als-business-{index:04d}",
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
        name="official-als-business-replay",
    )


def _hybrid(catalog):
    reference = RecommendationEngine(catalog)
    options = {
        "model": "als",
        "min_history": 3,
        "model_kwargs": {"iterations": 3, "random_state": 42},
    }
    adapter = ImplicitRecommendationAdapter(
        catalog,
        fallback=reference,
        **options,
    )
    return ImplicitHybridRecommendationEngine(
        reference,
        adapter,
        collaborative_limit=16,
        adapter_options=options,
    )


def test_official_als_hybrid_keeps_harness_ranking_and_guardrail_signals():
    engine = _hybrid(_catalog())

    results = engine.recommend("u-lin", limit=6)

    assert results
    assert all(row["backend"] == "hybrid_implicit_als" for row in results)
    assert all(row["collaborative_model"] == "als" for row in results)
    assert all("novelty" in row["signals"] for row in results)
    assert all("collaborative" in row["signals"] for row in results)
    assert any(row["signals"]["collaborative"] > 0 for row in results)


def test_official_als_model_is_reused_across_strategy_candidates():
    engine = _hybrid(_catalog())
    candidate = engine.with_config(
        RecommendConfig(
            profile_strategy="recent_intent",
            candidate_strategy="evidence_union",
            rerank_strategy="hybrid_mmr",
        )
    )

    assert candidate.adapter is engine.adapter
    assert candidate.recommend("u-lin", limit=5)
    assert engine.recommend("u-chen", limit=5)


def test_official_als_hybrid_runs_shared_domain_and_temporal_relevance_audits():
    catalog = _catalog()
    engine = _hybrid(catalog)

    domain = audit_recommend(catalog, engine)
    relevance = audit_recommend_relevance(catalog, engine, k=6)

    assert domain["users"] >= 3
    assert domain["coverage"] > 0
    assert domain["cold_start_samples"] == 3
    assert relevance["available"] is True
    assert relevance["users"] >= 3
    assert relevance["protocol"] == "strict_temporal_leave_one_out"


def test_official_als_hybrid_participates_in_evolution_without_retraining_per_config(monkeypatch):
    catalog = _catalog()
    engine = _hybrid(catalog)
    adapter = engine.adapter
    original_for_catalog = engine.for_catalog
    temporal_builds = 0

    def counted_for_catalog(training_catalog):
        nonlocal temporal_builds
        temporal_builds += 1
        return original_for_catalog(training_catalog)

    monkeypatch.setattr(engine, "for_catalog", counted_for_catalog)
    result = evolve_recommend(catalog, engine)

    assert result["evaluation_ready"] is True
    assert result["candidate_count"] > 0
    assert engine.adapter is adapter
    assert result["candidate_config"]
    assert temporal_builds == result["relevance_validation"]["prepared_slices"]


def test_official_als_business_evolution_bounds_only_optimizer_discovery():
    catalog = _production_catalog()
    engine = _hybrid(catalog)
    adapter = engine.adapter

    result = evolve_recommend(catalog, engine)
    business = result["business_validation"]

    assert business["available"] is True
    assert business["discovery_requests"] == MAX_BUSINESS_OPTIMIZER_REQUESTS == 64
    assert business["holdout_requests"] == 22
    assert business["confidence"]["samples"] == 22
    assert result["candidate"]["business_requests"] == 88
    assert result["reference"]["business_requests"] == 88
    assert engine.adapter is adapter


def test_explicit_runtime_registry_uses_official_als_for_active_strategy_serving():
    catalog = _catalog()
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)
    config = RecommendConfig(rerank_strategy="hybrid_mmr")
    memory.remember_strategy(
        key,
        "recommend",
        asdict(config),
        score=1.0,
        evidence=12,
        status="active",
        payload={"validated_at": time.time()},
    )

    registry = RecommendationBackendToolRegistry(
        catalog,
        memory,
        recommend_backend="implicit_als",
        recommend_backend_kwargs={
            "min_history": 3,
            "collaborative_limit": 16,
            "model_kwargs": {"iterations": 3, "random_state": 42},
        },
    )
    result = registry.run_recommend("u-lin")

    assert isinstance(registry.recommend, ImplicitHybridRecommendationEngine)
    assert registry.recommend.config.rerank_strategy == "hybrid_mmr"
    assert result["results"]
    assert all(row["backend"] == "hybrid_implicit_als" for row in result["results"])
    manifest = registry.inspect_data()["recommend_backend"]
    assert manifest["model"] == "als"
    assert manifest["ranking_owner"] == "harness"
    assert manifest["collaborative_owner"] == "implicit"
