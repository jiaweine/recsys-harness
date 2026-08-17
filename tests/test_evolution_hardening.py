from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import audit_cold_start, audit_recommend
from lingjing_harness.algorithms.evolution import (
    _evolution_schema,
    _perturb,
    _project,
    _recommend_gates,
    _stable_split,
    evolve_search,
)
from lingjing_harness.algorithms.recommend import RecommendConfig, RecommendationEngine
from lingjing_harness.algorithms.search import SearchConfig, SearchEngine
from lingjing_harness.domain import Catalog, Interaction, QueryLabel
from lingjing_harness.runtime.memory import AgentMemory, catalog_fingerprint
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def test_duplicate_query_labels_are_merged_before_any_holdout_split():
    source = build_sample_catalog()
    first = source.query_labels[0]
    extra_relevant = source.items[-1].item_id
    catalog = Catalog(
        items=list(source.items),
        interactions=list(source.interactions),
        query_labels=[
            *source.query_labels,
            QueryLabel(first.query, [extra_relevant]),
        ],
        name=source.name,
    )
    matches = [label for label in catalog.query_labels if label.query == first.query]
    assert len(matches) == 1
    assert set(first.relevant) <= set(matches[0].relevant)
    assert extra_relevant in matches[0].relevant


def test_stable_split_never_places_duplicate_identity_on_both_sides():
    rows = [
        QueryLabel("same", ["a"]),
        QueryLabel("same", ["b"]),
        QueryLabel("q2", ["a"]),
        QueryLabel("q3", ["a"]),
        QueryLabel("q4", ["a"]),
        QueryLabel("q5", ["a"]),
    ]
    discovery, holdout = _stable_split(rows, lambda row: row.query)
    left = {row.query for row in discovery}
    right = {row.query for row in holdout}
    assert not (left & right)
    assert sum(row.query == "same" for row in [*discovery, *holdout]) == 1


def test_projection_preserves_exact_blend_mass_even_after_extreme_clipping():
    config = SearchConfig()
    dimensions, totals = _evolution_schema(config)
    candidate = asdict(config)
    blend = [
        dimension
        for dimension in dimensions
        if dimension.kind == "continuous" and dimension.group == "blend"
    ]
    for dimension in blend:
        candidate[dimension.name] = dimension.high * 10
    projected = _project(candidate, dimensions, totals)
    assert abs(sum(projected[d.name] for d in blend) - totals["blend"]) < 1e-7
    assert all(d.low <= projected[d.name] <= d.high for d in blend)


def test_cold_start_gene_is_independent_from_warm_ranking_blend():
    config = RecommendConfig()
    base = asdict(config)
    dimensions, totals = _evolution_schema(config)
    cold = next(dimension for dimension in dimensions if dimension.name == "cold_start")
    assert cold.group == "independent"
    mutated = _perturb(base, cold, 1, dimensions, totals)
    warm_blend = [
        dimension.name
        for dimension in dimensions
        if dimension.kind == "continuous" and dimension.group == "blend"
    ]
    assert mutated["cold_start"] > base["cold_start"]
    assert all(mutated[name] == pytest.approx(base[name]) for name in warm_blend)


def test_corrupted_trusted_memory_cannot_crash_new_evolution_run():
    catalog = build_sample_catalog()
    result = evolve_search(
        catalog,
        SearchEngine(catalog),
        remembered=[
            {
                "status": "trusted",
                "wins": 5,
                "config": {
                    "lexical": "not-a-number",
                    "query_strategy": "removed-capability",
                },
            }
        ],
    )
    assert result["evaluation_ready"] is True
    assert result["evolution"]["hardening_version"] == 2


def test_cold_start_probe_avoids_real_user_identity_collision():
    source = build_sample_catalog()
    collision_user = "__harness_cold_eval__:holdout:0"
    catalog = Catalog(
        items=list(source.items),
        interactions=[
            *source.interactions,
            Interaction(collision_user, source.items[0].item_id, "click", 1.0, 1.0),
        ],
        query_labels=list(source.query_labels),
        name=source.name,
    )
    report = audit_cold_start(
        catalog,
        RecommendationEngine(catalog),
        slice_key="holdout",
        samples=3,
    )
    assert report["samples"] == 3
    assert report["collision_avoided"] >= 1
    assert report["quality"] > 0


def test_recommend_trust_can_be_earned_by_cold_start_improvement_only():
    safe, trusted = _recommend_gates(
        users=5,
        q_delta=0.0,
        cov_delta=0.0,
        fresh_delta=0.0,
        div_delta=0.0,
        cold_delta=0.02,
        discovery_delta=0.01,
        robust={"worse_share": 0.0, "worst_delta": 0.0},
        holdout_available=True,
        holdout_q_delta=0.0,
        holdout_cov_delta=0.0,
        holdout_cold_delta=0.01,
        holdout_robust={"worse_share": 0.0, "worst_delta": 0.0},
    )
    assert safe is True
    assert trusted is True


def test_holdout_cold_start_regression_blocks_strategy_even_if_warm_metrics_improve():
    safe, trusted = _recommend_gates(
        users=5,
        q_delta=0.02,
        cov_delta=0.03,
        fresh_delta=0.02,
        div_delta=0.02,
        cold_delta=0.01,
        discovery_delta=0.03,
        robust={"worse_share": 0.0, "worst_delta": 0.0},
        holdout_available=True,
        holdout_q_delta=0.02,
        holdout_cov_delta=0.03,
        holdout_cold_delta=-0.08,
        holdout_robust={"worse_share": 0.0, "worst_delta": 0.0},
    )
    assert safe is False
    assert trusted is False


def test_public_recommend_audit_reports_real_cold_start_slice():
    catalog = build_sample_catalog()
    report = audit_recommend(catalog, RecommendationEngine(catalog))
    assert report["users"] >= 3
    assert report["cold_start_samples"] == 3
    assert report["cold_start_quality"] > 0


def test_invalid_active_strategy_is_retired_before_runtime_execution():
    catalog = build_sample_catalog()
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)
    raw = asdict(SearchConfig())
    raw["query_strategy"] = "capability-that-no-longer-exists"
    memory.remember_strategy(
        key,
        "search",
        raw,
        score=1.0,
        evidence=10,
        status="active",
    )

    tools = ToolRegistry(catalog, memory)
    assert tools.search.config == SearchConfig()
    assert memory.active_skill(key, "search") is None
    assert any(row["domain"] == "search" for row in tools.rollback_events)


def test_active_recommendation_rolls_back_on_cold_start_regression(monkeypatch):
    catalog = build_sample_catalog()
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)
    raw = asdict(RecommendConfig())
    raw["cold_start_strategy"] = "fresh_explore"
    memory.remember_strategy(
        key,
        "recommend",
        raw,
        score=1.0,
        evidence=10,
        status="active",
        payload={"validated_at": 0.0},
    )

    import lingjing_harness.runtime.tools_core as tools_core

    def fake_audit(_catalog, engine):
        degraded = engine.config.cold_start_strategy == "fresh_explore"
        return {
            "users": 5,
            "quality": 0.8,
            "coverage": 0.8,
            "cold_start_quality": 0.40 if degraded else 0.80,
        }

    monkeypatch.setattr(tools_core, "audit_recommend", fake_audit)
    tools = ToolRegistry(catalog, memory)
    assert tools.recommend.config == RecommendConfig()
    assert memory.active_skill(key, "recommend") is None
    assert any(row["domain"] == "recommend" for row in tools.rollback_events)
