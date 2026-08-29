from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.recommend import RecommendConfig, RecommendationEngine
from lingjing_harness.algorithms.search import SearchConfig, SearchEngine
from lingjing_harness.sample_data import build_sample_catalog


def test_search_capability_registry_is_vertical_and_default_behavior_is_stable():
    catalog = build_sample_catalog()
    engine = SearchEngine(catalog)
    manifest = engine.capability_manifest()
    assert {"search.query", "search.candidate", "search.rerank"} <= set(manifest)
    rows = engine.search("露营灯", limit=5)
    assert rows
    assert rows[0]["id"] in {"p05", "p06"}
    assert engine.search("完全不存在的词", limit=5) == []


def test_search_structural_genome_can_switch_real_query_retrieval_and_rerank_stages():
    catalog = build_sample_catalog()
    config = SearchConfig(
        query_strategy="catalog_expand",
        candidate_strategy="semantic_rescue",
        rerank_strategy="hybrid_mmr",
    )
    engine = SearchEngine(catalog, config)
    rows = engine.search("露营灯", limit=5)
    assert rows
    assert {row["id"] for row in rows[:4]} & {"p05", "p06"}
    assert config.query_strategy in CAPABILITIES.names("search.query")
    assert config.candidate_strategy in CAPABILITIES.names("search.candidate")
    assert config.rerank_strategy in CAPABILITIES.names("search.rerank")


def test_recommend_structural_genome_switches_profile_candidate_exploration_and_rerank():
    catalog = build_sample_catalog()
    config = RecommendConfig(
        profile_strategy="recent_intent",
        candidate_strategy="evidence_union",
        exploration_strategy="novelty_seek",
        rerank_strategy="hybrid_mmr",
    )
    engine = RecommendationEngine(catalog, config)
    seen = {
        event.item_id
        for event in catalog.interactions
        if event.user_id == "u-lin"
    }
    rows = engine.recommend("u-lin", limit=8)
    assert rows
    assert not (seen & {row["id"] for row in rows})
    assert len(rows) >= 6


def test_recommend_serving_is_a_real_strategy_gene_with_owned_default():
    catalog = build_sample_catalog()
    engine = RecommendationEngine(catalog)
    manifest = engine.capability_manifest()

    assert engine.config.serving_strategy == "reference"
    assert "recommend.serving" in manifest
    assert "reference" in CAPABILITIES.names("recommend.serving")
    assert engine.recommend("u-lin", limit=4)


def test_unknown_recommend_serving_capability_fails_closed_to_reference():
    catalog = build_sample_catalog()
    engine = RecommendationEngine(
        catalog,
        RecommendConfig(serving_strategy="removed-serving-backend"),
    )

    assert engine.config.serving_strategy == "reference"
    assert engine.recommend("u-lin", limit=4)


def test_cold_start_capability_is_a_real_stage_not_a_readme_label():
    catalog = build_sample_catalog()
    base = RecommendationEngine(catalog, RecommendConfig())
    discovery = RecommendationEngine(
        catalog,
        RecommendConfig(
            cold_start_strategy="discovery_prior",
            exploration_strategy="coverage_seek",
        ),
    )
    base_rows = base.recommend("brand-new-user", limit=8)
    discovery_rows = discovery.recommend("brand-new-user", limit=8)
    assert base_rows and discovery_rows
    assert [
        (row["id"], row["score"]) for row in base_rows
    ] != [
        (row["id"], row["score"]) for row in discovery_rows
    ]


def test_unknown_persisted_capability_fails_closed_to_owned_default():
    catalog = build_sample_catalog()
    engine = SearchEngine(
        catalog,
        SearchConfig(query_strategy="removed_strategy"),
    )
    rows = engine.search("露营灯", limit=3)
    assert rows
    assert rows[0]["id"] in {"p05", "p06"}
