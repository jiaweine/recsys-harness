from dataclasses import asdict, dataclass, fields

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.capabilities import CAPABILITIES, capability_field
from lingjing_harness.algorithms.evolution import (
    _evolution_schema,
    _history_posteriors,
    _perturb,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.algorithms.recommend import RecommendConfig, RecommendationEngine
from lingjing_harness.algorithms.recommend_objective_routing import (
    MIN_RELEVANCE_OBJECTIVE_EVIDENCE,
)
from lingjing_harness.algorithms.search import SearchConfig, SearchEngine
from lingjing_harness.domain import Catalog, Interaction
from lingjing_harness.sample_data import build_sample_catalog


def _continuous_fields(config):
    return [row for row in fields(config) if row.metadata.get("evolve_group")]


def _capability_fields(config):
    return [row for row in fields(config) if row.metadata.get("capability_group")]


def _expanded_temporal_catalog() -> Catalog:
    base = build_sample_catalog()
    duplicated = [
        Interaction(
            user_id=f"copy-{event.user_id}",
            item_id=event.item_id,
            event=event.event,
            weight=event.weight,
            timestamp=event.timestamp,
        )
        for event in base.interactions
    ]
    return Catalog(
        items=list(base.items),
        interactions=[*base.interactions, *duplicated],
        query_labels=list(base.query_labels),
        name="expanded temporal evolution fixture",
    )


def test_search_evolution_schema_is_declared_by_config_fields():
    config = SearchConfig()
    dimensions, group_totals = _evolution_schema(config)
    assert {row.name for row in dimensions} == {row.name for row in fields(config)}
    assert group_totals["blend"] == sum(
        getattr(config, row.name)
        for row in _continuous_fields(config)
        if row.metadata.get("evolve_group") == "blend"
    )
    assert _capability_fields(config)
    assert all(
        row.metadata.get("evolve_group") or row.metadata.get("capability_group")
        for row in fields(config)
    )


def test_recommend_evolution_schema_is_declared_by_config_fields():
    config = RecommendConfig()
    dimensions, group_totals = _evolution_schema(config)
    assert {row.name for row in dimensions} == {row.name for row in fields(config)}
    assert group_totals["blend"] == sum(
        getattr(config, row.name)
        for row in _continuous_fields(config)
        if row.metadata.get("evolve_group") == "blend"
    )
    assert len(_capability_fields(config)) >= 5


def test_schema_projection_preserves_blend_mass_without_parameter_recipes():
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = _evolution_schema(config)
    lexical = next(row for row in dimensions if row.name == "lexical")
    candidate = _perturb(base, lexical, 1, dimensions, group_totals)
    blend_names = [
        row.name
        for row in dimensions
        if row.kind == "continuous" and row.group == "blend"
    ]
    assert candidate["lexical"] > base["lexical"]
    assert abs(sum(candidate[name] for name in blend_names) - group_totals["blend"]) < 1e-7


def test_validated_strategy_memory_becomes_dynamic_direction_and_capability_prior():
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = _evolution_schema(config)
    remembered = [{
        "config": {
            **base,
            "lexical": base["lexical"] + 0.12,
            "semantic": base["semantic"] - 0.12,
            "rerank_strategy": "semantic_mmr",
        },
        "wins": 4,
        "status": "trusted",
    }]
    posterior = _history_posteriors(base, remembered, dimensions, group_totals)
    up_alpha, up_beta = posterior["lexical:up"]
    down_alpha, down_beta = posterior["lexical:down"]
    cap_alpha, cap_beta = posterior["rerank_strategy=semantic_mmr"]
    assert up_alpha > up_beta
    assert down_beta > down_alpha
    assert cap_alpha > cap_beta


def test_capability_schema_discovers_registry_choices_without_central_arm_list():
    group = "test.vertical.capability"
    if not CAPABILITIES.names(group):
        CAPABILITIES.register(group, "owned_a", "test A", lambda: "a", default=True)
        CAPABILITIES.register(group, "owned_b", "test B", lambda: "b")

    @dataclass(frozen=True)
    class DemoGenome:
        strategy: str = capability_field(group, "owned_a")

    dimensions, _ = _evolution_schema(DemoGenome())
    assert len(dimensions) == 1
    assert dimensions[0].kind == "capability"
    assert dimensions[0].choices == ("owned_a", "owned_b")


def test_search_evolution_measures_continuous_and_structural_response_surface():
    catalog = build_sample_catalog()
    result = evolve_search(catalog, SearchEngine(catalog))
    meta = result["evolution"]
    assert result["evaluation_ready"] is True
    assert meta["method"] == "mixed_genome_response_surface"
    assert meta["router"] == "posterior_guided_mixed_arms"
    assert meta["domain_driven"] is True
    assert meta["handwritten_mutation_recipes"] is False
    assert meta["central_capability_preferences"] is False
    assert {"continuous", "capability"} <= {
        row["kind"] for row in meta["response_surface"]
    }
    continuous = [row for row in meta["response_surface"] if row["kind"] == "continuous"]
    structural = [row for row in meta["response_surface"] if row["kind"] == "capability"]
    assert {row["direction"] for row in continuous} == {"up", "down"}
    assert structural
    assert result["validation"]["holdout"]["independent"] is True
    assert meta["archive_size"] >= 1
    assert set(meta["selected_capabilities"]) == {
        "query_strategy",
        "candidate_strategy",
        "rerank_strategy",
    }


def test_recommend_objective_router_is_exact_noop_outside_evolution_scope():
    report = {
        "quality": 0.72,
        "freshness": 0.63,
        "diversity": 0.54,
        "cold_start_quality": 0.48,
    }
    robust = {"worse_share": 0.2, "worst_delta": -0.1}
    expected = (
        0.72
        + 0.05 * 0.63
        + 0.03 * 0.54
        + 0.04 * 0.48
        - 0.03 * 0.2
        + 0.01 * -0.1
    )
    assert core._recommend_objective(report, robust) == pytest.approx(expected)


def test_sparse_discovery_relevance_does_not_drive_optimizer():
    catalog = build_sample_catalog()
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    relevance = result["evolution"]["relevance_objective"]

    assert relevance["available"] is False
    assert relevance["reason"] == "insufficient_discovery_evidence"
    assert relevance["samples"] == 4
    assert relevance["minimum_samples"] == MIN_RELEVANCE_OBJECTIVE_EVIDENCE == 6
    assert result["trusted"] is True


def test_recommend_evolution_includes_cold_start_slice_and_structural_genes():
    catalog = build_sample_catalog()
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    meta = result["evolution"]
    assert result["evaluation_ready"] is True
    assert meta["method"] == "mixed_genome_response_surface"
    assert meta["capability_dimensions"] >= 5
    assert result["reference"]["cold_start_quality"] > 0
    assert "cold_start_quality" in result["delta"]
    assert result["validation"]["holdout"]["independent"] is True
    assert any(
        row["field"] == "cold_start_strategy" and row["kind"] == "capability"
        for row in meta["response_surface"]
    )
    assert meta["archive_size"] >= 1


def test_recommend_relevance_objective_activates_with_stronger_temporal_evidence():
    catalog = _expanded_temporal_catalog()
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    meta = result["evolution"]
    relevance = meta["relevance_objective"]

    assert relevance["available"] is True
    assert relevance["scope"] == "discovery_users_only"
    assert relevance["protocol"] == "strict_temporal_leave_one_out"
    assert relevance["samples"] >= MIN_RELEVANCE_OBJECTIVE_EVIDENCE
    assert relevance["minimum_samples"] == MIN_RELEVANCE_OBJECTIVE_EVIDENCE
    assert relevance["aggregation"] == "mean_delta_ndcg_mrr"

    surface = {row["arm"]: row for row in meta["response_surface"]}
    assert surface["rerank_strategy=semantic_mmr"]["objective_delta"] > surface["exploration:up"]["objective_delta"]
