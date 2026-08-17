import math

import pytest

from lingjing_harness.algorithms import (
    RecommendationEngine,
    SearchEngine,
    audit_recommend,
    evolve_recommend,
    evolve_search,
)
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.runtime import AgentHarness, OwnedPolicy


def test_catalog_rejects_rows_that_become_empty_after_validation():
    with pytest.raises(ValueError, match="至少需要一条"):
        Catalog.from_payload({"items": [{"id": "", "title": ""}]})


def test_catalog_rejects_bad_shapes_and_non_finite_numbers():
    with pytest.raises(ValueError, match="items 必须是数组"):
        Catalog.from_payload({"items": "bad"})
    with pytest.raises(ValueError, match="有限数值"):
        Catalog.from_payload({"items": [{"id": "a", "title": "A", "popularity": float("inf")}]})
    with pytest.raises(ValueError, match="有限数值"):
        Catalog.from_payload({"items": [{"id": "a", "title": "A", "quality": float("nan")}]})


def test_catalog_parses_explicit_false_eligibility():
    catalog = Catalog.from_payload({"items": [{"id": "a", "title": "A", "eligible": "false"}]})
    assert catalog.items[0].eligible is False


def test_query_labels_ignore_ineligible_targets():
    catalog = Catalog.from_payload({
        "items": [
            {"id": "a", "title": "A", "eligible": False},
            {"id": "b", "title": "B"},
        ],
        "query_labels": [{"query": "A", "relevant": ["a"]}],
    })
    assert catalog.query_labels == []


def test_explicit_unknown_user_is_not_replaced_by_another_user():
    catalog = Catalog(
        items=[Item("a", "A"), Item("b", "B")],
        interactions=[Interaction("known", "a")],
    )
    plan = OwnedPolicy().plan("看看用户 stranger 的推荐", catalog)
    assert plan.user_id == "stranger"
    result = AgentHarness(catalog).run("看看用户 stranger 的推荐")
    rec_action = next(x for x in result["actions"] if x["tool"] == "recommend.run")
    assert rec_action["result"]["user_id"] == "stranger"


def test_evolution_requires_real_evaluation_evidence():
    catalog = Catalog(items=[Item("a", "A"), Item("b", "B")])
    search = evolve_search(catalog, SearchEngine(catalog))
    recommend = evolve_recommend(catalog, RecommendationEngine(catalog))
    assert search["evaluation_ready"] is False
    assert search["safe_to_try"] is False
    assert recommend["evaluation_ready"] is False
    assert recommend["safe_to_try"] is False


def test_recommendation_coverage_uses_eligible_catalog_only():
    catalog = Catalog(
        items=[Item("a", "A"), Item("b", "B", eligible=False), Item("c", "C")],
        interactions=[Interaction("u", "a")],
    )
    report = audit_recommend(catalog, RecommendationEngine(catalog))
    assert report["coverage"] == 0.5


def test_search_and_recommend_stay_finite_on_valid_extremes():
    catalog = Catalog.from_payload({
        "items": [
            {"id": "a", "title": "露营灯", "popularity": 1e100, "quality": 1, "freshness": 1},
            {"id": "b", "title": "户外灯", "popularity": 0, "quality": 0, "freshness": 0},
        ],
        "interactions": [{"user_id": "u", "item_id": "b", "event": "click", "timestamp": 1}],
    })
    for row in SearchEngine(catalog).search("露营灯"):
        assert math.isfinite(row["score"])
    for row in RecommendationEngine(catalog).recommend("u"):
        assert math.isfinite(row["score"])


def test_graph_build_is_bounded_for_long_user_history():
    items = [Item(str(i), f"item {i}") for i in range(180)]
    events = [Interaction("u", str(i), timestamp=float(i)) for i in range(180)]
    engine = RecommendationEngine(Catalog(items=items, interactions=events))
    assert len(engine._co) <= engine.MAX_GRAPH_HISTORY


def test_audits_bound_large_evaluation_sets():
    from lingjing_harness.algorithms.evaluation import MAX_AUDIT_QUERIES, MAX_AUDIT_USERS, audit_search
    items=[Item(str(i),f"item {i} query") for i in range(60)]
    events=[Interaction(f"u{i}",str(i%60),timestamp=1) for i in range(60)]
    from lingjing_harness.domain import QueryLabel
    labels=[QueryLabel(f"query {i}",[str(i%60)]) for i in range(60)]
    catalog=Catalog(items,events,labels)
    sr=audit_search(catalog,SearchEngine(catalog))
    rr=audit_recommend(catalog,RecommendationEngine(catalog))
    assert sr["queries"] == MAX_AUDIT_QUERIES and sr["sampled"] is True
    assert rr["users"] == MAX_AUDIT_USERS and rr["sampled"] is True


def test_evolution_rejects_too_little_evidence():
    from lingjing_harness.domain import QueryLabel
    catalog=Catalog(
        items=[Item("a","A"),Item("b","B"),Item("c","C")],
        interactions=[Interaction("u","a")],
        query_labels=[QueryLabel("A",["a"])],
    )
    assert evolve_search(catalog,SearchEngine(catalog))["safe_to_try"] is False
    assert evolve_recommend(catalog,RecommendationEngine(catalog))["safe_to_try"] is False


def test_bad_active_strategy_is_automatically_retired(tmp_path):
    from dataclasses import asdict
    from lingjing_harness.algorithms import RecommendConfig
    from lingjing_harness.runtime import AgentMemory, catalog_fingerprint
    from lingjing_harness.runtime.tools import ToolRegistry

    catalog = __import__("lingjing_harness.sample_data", fromlist=["build_sample_catalog"]).build_sample_catalog()
    memory = AgentMemory(tmp_path / "memory.db")
    key = catalog_fingerprint(catalog)
    bad = RecommendConfig(
        profile=.001, graph=.001, category=.001, quality=.001, freshness=.001,
        popularity=.99, novelty=.001, diversity=0.0, exploration=.001,
    )
    memory.remember_strategy(key, "recommend", asdict(bad), score=0.99, evidence=5, status="active")
    registry = ToolRegistry(catalog, memory)
    assert registry.rollback_events and registry.rollback_events[0]["domain"] == "recommend"
    assert memory.active_config(key, "recommend") is None
