from dataclasses import asdict, fields

from lingjing_harness.algorithms.evolution import (
    _evolution_schema,
    _history_posteriors,
    _perturb,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.algorithms.recommend import RecommendConfig, RecommendationEngine
from lingjing_harness.algorithms.search import SearchConfig, SearchEngine
from lingjing_harness.sample_data import build_sample_catalog


def test_search_evolution_schema_is_declared_by_config_fields():
    config = SearchConfig()
    dimensions, group_totals = _evolution_schema(config)
    assert {row.name for row in dimensions} == {row.name for row in fields(config)}
    assert group_totals["blend"] == sum(
        getattr(config, row.name) for row in fields(config) if row.metadata.get("evolve_group") == "blend"
    )
    assert all(row.metadata.get("evolve_group") for row in fields(config))


def test_recommend_evolution_schema_is_declared_by_config_fields():
    config = RecommendConfig()
    dimensions, group_totals = _evolution_schema(config)
    assert {row.name for row in dimensions} == {row.name for row in fields(config)}
    assert group_totals["blend"] == sum(
        getattr(config, row.name) for row in fields(config) if row.metadata.get("evolve_group") == "blend"
    )
    assert all(row.metadata.get("evolve_group") for row in fields(config))


def test_schema_projection_preserves_blend_mass_without_parameter_recipes():
    config = SearchConfig()
    base = asdict(config)
    dimensions, group_totals = _evolution_schema(config)
    lexical = next(row for row in dimensions if row.name == "lexical")
    candidate = _perturb(base, lexical, 1, dimensions, group_totals)
    blend_names = [row.name for row in dimensions if row.group == "blend"]
    assert candidate["lexical"] > base["lexical"]
    assert abs(sum(candidate[name] for name in blend_names) - group_totals["blend"]) < 1e-7


def test_validated_strategy_memory_becomes_dynamic_direction_prior():
    config = SearchConfig()
    base = asdict(config)
    dimensions, _ = _evolution_schema(config)
    remembered = [{
        "config": {**base, "lexical": base["lexical"] + 0.12, "semantic": base["semantic"] - 0.12},
        "wins": 4,
        "status": "trusted",
    }]
    posterior = _history_posteriors(base, remembered, dimensions)
    up_alpha, up_beta = posterior["lexical:up"]
    down_alpha, down_beta = posterior["lexical:down"]
    assert up_alpha > up_beta
    assert down_beta > down_alpha


def test_search_evolution_measures_real_response_surface_and_keeps_holdout_gate():
    catalog = build_sample_catalog()
    result = evolve_search(catalog, SearchEngine(catalog))
    meta = result["evolution"]
    assert result["evaluation_ready"] is True
    assert meta["method"] == "schema_response_surface"
    assert meta["router"] == "posterior_guided_dynamic_arms"
    assert meta["domain_driven"] is True
    assert meta["handwritten_mutation_recipes"] is False
    assert {row["field"] for row in meta["response_surface"]} == {row.name for row in _evolution_schema(SearchConfig())[0]}
    assert {row["direction"] for row in meta["response_surface"]} == {"up", "down"}
    assert result["validation"]["holdout"]["independent"] is True
    assert meta["archive_size"] >= 1


def test_recommend_evolution_measures_real_response_surface_and_keeps_holdout_gate():
    catalog = build_sample_catalog()
    result = evolve_recommend(catalog, RecommendationEngine(catalog))
    meta = result["evolution"]
    assert result["evaluation_ready"] is True
    assert meta["method"] == "schema_response_surface"
    assert meta["router"] == "posterior_guided_dynamic_arms"
    assert meta["domain_driven"] is True
    assert meta["handwritten_mutation_recipes"] is False
    assert {row["field"] for row in meta["response_surface"]} == {row.name for row in _evolution_schema(RecommendConfig())[0]}
    assert {row["direction"] for row in meta["response_surface"]} == {"up", "down"}
    assert result["validation"]["holdout"]["independent"] is True
    assert meta["archive_size"] >= 1
