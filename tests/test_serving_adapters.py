from lingjing_harness.adapters import (
    AdapterRecommendationEngine,
    AdapterSearchEngine,
    CallableRecommendAdapter,
    CallableSearchAdapter,
)
from lingjing_harness.production import ExposureEvent, RewardSpec, evaluate_logged_policy


def test_external_search_adapter_normalizes_duplicate_and_invalid_rows():
    adapter = CallableSearchAdapter(
        lambda query, limit: [
            {"item_id": "a", "score": 1.0},
            {"id": "a", "score": 0.9},
            {"id": "bad", "score": float("nan")},
            {"id": "b", "score": 0.7},
        ]
    )
    engine = AdapterSearchEngine(adapter)
    rows = engine.search("q", limit=10)
    assert [row["id"] for row in rows] == ["a", "b"]
    assert [row["rank"] for row in rows] == [1, 2]


def test_external_recommend_adapter_can_use_same_business_replay_contract():
    adapter = CallableRecommendAdapter(
        lambda user_id, limit: [
            {"id": "good", "score": 1.0},
            {"id": "other", "score": 0.5},
        ]
    )
    engine = AdapterRecommendationEngine(adapter)
    events = [
        ExposureEvent(
            request_id="r1",
            timestamp=1.0,
            surface="recommend",
            user_id="u1",
            item_id="good",
            event="purchase",
            value=1.0,
            propensity=0.5,
        )
    ]
    report = evaluate_logged_policy(
        events,
        surface="recommend",
        reward_spec=RewardSpec(weights={"purchase": 5.0}),
        recommend_engine=engine,
    )
    assert report["requests"] == 1
    assert report["reward"] == 1.0
    assert report["reward_coverage"] == 1.0
