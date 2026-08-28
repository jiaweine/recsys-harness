from dataclasses import asdict

from lingjing_harness.algorithms import (
    RecommendationEngine,
    audit_recommend,
    audit_recommend_relevance,
    prepare_recommend_relevance,
)
from lingjing_harness.algorithms.recommend import RecommendConfig
from lingjing_harness.domain import Catalog, Interaction, Item
from lingjing_harness.sample_data import build_sample_catalog


def test_sample_recommender_beats_popularity_on_temporal_relevance():
    catalog = build_sample_catalog()
    report = audit_recommend_relevance(catalog, RecommendationEngine(catalog), k=10)

    assert report["available"] is True
    assert report["users"] == 5
    assert report["protocol"] == "strict_temporal_leave_one_out"
    assert report["temporal_scope"] == "interactions_only"
    assert report["point_in_time_item_features"] is False
    assert report["minimum_target_weight"] == 1.0
    assert report["model"]["hit_rate"] >= report["popularity_baseline"]["hit_rate"]
    assert report["model"]["ndcg"] > report["popularity_baseline"]["ndcg"]
    assert report["delta_vs_popularity"]["ndcg"] > 0.0


def test_prepared_relevance_reuses_temporal_slices_across_configs():
    catalog = build_sample_catalog()
    engine = RecommendationEngine(catalog)
    prepared = prepare_recommend_relevance(catalog, engine, k=10)
    reference = prepared.evaluate(engine.config)
    same = prepared.evaluate(RecommendConfig())

    assert reference == same
    assert reference["prepared_slices"] == 5
    assert len(prepared.slices) == 5


def test_weak_view_does_not_become_default_relevance_target():
    catalog = Catalog(
        items=[
            Item("a", "A", popularity=10),
            Item("b", "B", popularity=9),
        ],
        interactions=[
            Interaction("u", "a", "click", 1.0, 10.0),
            Interaction("u", "b", "view", 0.2, 20.0),
        ],
        name="weak-view-target",
    )
    report = audit_recommend_relevance(catalog, RecommendationEngine(catalog))
    assert report["available"] is False
    assert report["users"] == 0


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


def test_relevance_regression_blocks_candidate_promotion(monkeypatch):
    import lingjing_harness.algorithms.evolution as evolution_module

    class FakePrepared:
        def __init__(self):
            self.calls = 0

        def evaluate(self, _config):
            self.calls += 1
            model = {"ndcg": 0.40, "mrr": 0.40} if self.calls == 1 else {"ndcg": 0.20, "mrr": 0.20}
            return {
                "available": True,
                "users": 5,
                "protocol": "strict_temporal_leave_one_out",
                "temporal_scope": "interactions_only",
                "point_in_time_item_features": False,
                "minimum_target_weight": 1.0,
                "prepared_slices": 5,
                "model": model,
            }

    prepared = FakePrepared()
    monkeypatch.setattr(
        evolution_module,
        "prepare_recommend_relevance",
        lambda *_args, **_kwargs: prepared,
    )
    catalog = build_sample_catalog()
    current = RecommendationEngine(catalog)
    candidate = RecommendConfig(rerank_strategy="semantic_mmr")
    result = evolution_module._recommend_relevance_gate(
        catalog,
        current,
        {
            "evaluation_ready": True,
            "safe_to_try": True,
            "trusted": True,
            "business_trusted": True,
            "candidate_config": asdict(candidate),
        },
    )

    assert result["relevance_guardrail_passed"] is False
    assert result["safe_to_try"] is False
    assert result["trusted"] is False
    assert result["business_trusted"] is False
    assert "recommend_relevance_regression" in result["trust_blocked_by"]
