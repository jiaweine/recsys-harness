from lingjing_harness.algorithms import (
    RecommendationEngine,
    audit_recommend,
    audit_recommend_relevance,
)
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.sample_data import build_sample_catalog


def test_sample_recommender_beats_popularity_on_temporal_relevance():
    catalog = build_sample_catalog()
    report = audit_recommend_relevance(catalog, RecommendationEngine(catalog), k=10)

    assert report["available"] is True
    assert report["users"] == 5
    assert report["protocol"] == "strict_temporal_leave_one_out"
    assert report["model"]["hit_rate"] >= report["popularity_baseline"]["hit_rate"]
    assert report["model"]["ndcg"] > report["popularity_baseline"]["ndcg"]
    assert report["delta_vs_popularity"]["ndcg"] > 0.0


def test_public_recommend_audit_exposes_accuracy_not_only_guardrails():
    catalog = build_sample_catalog()
    report = audit_recommend(catalog, RecommendationEngine(catalog))

    assert report["relevance_available"] is True
    assert report["relevance_users"] == 5
    assert report["relevance_k"] == 10
    assert 0.0 <= report["relevance_ndcg"] <= 1.0
    assert 0.0 <= report["relevance_mrr"] <= 1.0
    assert "popularity_relevance_ndcg" in report
    assert "relevance_ndcg_delta_vs_popularity" in report


def test_temporal_relevance_requires_history_before_target():
    catalog = Catalog(
        items=[
            Item("a", "A", popularity=10),
            Item("b", "B", popularity=9),
        ],
        interactions=[Interaction("u", "a", "click", 1.0, 10.0)],
        name="single-event",
    )
    report = audit_recommend_relevance(catalog, RecommendationEngine(catalog))
    assert report["available"] is False
    assert report["users"] == 0
