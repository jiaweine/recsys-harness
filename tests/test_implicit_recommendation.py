from __future__ import annotations

from dataclasses import asdict
from math import isfinite
import time

import pytest

pytest.importorskip("implicit")

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    audit_recommend_relevance,
)
from lingjing_harness.algorithms.capabilities import CAPABILITIES
from lingjing_harness.algorithms.evolution import _evolution_schema
from lingjing_harness.domain import Interaction
from lingjing_harness.integrations import ImplicitRecommendationAdapter
from lingjing_harness.integrations.implicit_recommendation import (
    DEFAULT_IMPLICIT_MODEL,
    DEFAULT_MIN_HISTORY,
)
from lingjing_harness.runtime.memory import AgentMemory, catalog_fingerprint
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def _add_collaborative_overlap(catalog) -> None:
    """Give item-item CF explicit cross-user bridges into unseen items."""

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


@pytest.mark.parametrize(
    ("model", "model_kwargs"),
    [
        ("bpr", {"iterations": 5, "num_threads": 1, "random_state": 42}),
        ("als", {"iterations": 5, "random_state": 42}),
        ("bm25", {}),
    ],
)
def test_implicit_models_rank_warm_users_and_filter_seen_items(model, model_kwargs):
    catalog = build_sample_catalog()
    _add_collaborative_overlap(catalog)
    catalog.item_by_id["p02"].eligible = False
    adapter = ImplicitRecommendationAdapter(
        catalog,
        model=model,
        min_history=3,
        model_kwargs=model_kwargs,
    )

    results = adapter.recommend("u-lin", limit=6)
    seen = {event.item_id for event in catalog.interactions if event.user_id == "u-lin"}

    assert results
    assert len({row["id"] for row in results}) == len(results)
    assert all(row["id"] not in seen for row in results)
    assert all(row["id"] != "p02" for row in results)
    assert results[0]["backend"] == f"implicit_{model}"
    assert adapter.capability_manifest()["training_interactions"] == len(catalog.interactions)


def test_default_collaborative_contract_uses_als_from_three_positive_interactions():
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(
        catalog,
        model_kwargs={"iterations": 3, "random_state": 42},
    )

    assert DEFAULT_IMPLICIT_MODEL == "als"
    assert DEFAULT_MIN_HISTORY == 3
    assert adapter.model_name == "als"
    assert adapter.min_history == 3
    assert adapter.capability_manifest()["model"] == "als"
    assert adapter.capability_manifest()["min_history"] == 3
    assert adapter.history_count("u-chen") == 4
    assert adapter.recommend("u-chen", limit=4)[0]["backend"] == "implicit_als"


def test_implicit_als_is_an_evolvable_recommend_serving_capability():
    dimensions, _ = _evolution_schema(RecommendConfig())
    serving = next(dimension for dimension in dimensions if dimension.name == "serving_strategy")

    assert serving.kind == "capability"
    assert serving.group == "recommend.serving"
    assert "reference" in serving.choices
    assert "implicit_als" in serving.choices
    assert "implicit_als" in CAPABILITIES.names("recommend.serving")


def test_routed_implicit_als_serves_warm_users_and_reuses_trained_model_cache():
    catalog = build_sample_catalog()
    _add_collaborative_overlap(catalog)
    root = RecommendationEngine(catalog)
    config = RecommendConfig(serving_strategy="implicit_als")

    first = root.with_config(config)
    first_rows = first.recommend("u-lin", limit=6)

    assert first.config.serving_strategy == "implicit_als"
    assert first_rows
    assert any(row.get("backend") == "implicit_als" for row in first_rows)
    assert all("novelty" in row.get("signals", {}) for row in first_rows)
    assert len(root._serving_backend_cache) == 1

    second = root.with_config(config)
    second_rows = second.recommend("u-chen", limit=4)

    assert second._serving_backend_cache is root._serving_backend_cache
    assert len(root._serving_backend_cache) == 1
    assert second_rows
    assert any(row.get("backend") == "implicit_als" for row in second_rows)


def test_routed_implicit_als_enters_temporal_relevance_guardrail():
    catalog = build_sample_catalog()
    _add_collaborative_overlap(catalog)
    engine = RecommendationEngine(
        catalog,
        RecommendConfig(serving_strategy="implicit_als"),
    )

    report = audit_recommend_relevance(catalog, engine, k=6)

    assert report["available"] is True
    assert report["users"] >= 3
    assert report["protocol"] == "strict_temporal_leave_one_out"
    assert report["model"]["ndcg"] >= 0.0


def test_durable_active_implicit_strategy_is_the_runtime_serving_path():
    catalog = build_sample_catalog()
    _add_collaborative_overlap(catalog)
    memory = AgentMemory()
    key = catalog_fingerprint(catalog)
    config = RecommendConfig(serving_strategy="implicit_als")
    memory.remember_strategy(
        key,
        "recommend",
        asdict(config),
        score=1.0,
        evidence=12,
        status="active",
        payload={"validated_at": time.time()},
    )

    tools = ToolRegistry(catalog, memory)
    result = tools.run_recommend("u-lin")

    assert tools.recommend.config.serving_strategy == "implicit_als"
    assert result["results"]
    assert any(row.get("backend") == "implicit_als" for row in result["results"])


def test_sparse_and_unknown_users_use_reference_fallback():
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(catalog, model="bpr", min_history=5, model_kwargs={"iterations": 3})

    sparse = adapter.recommend("u-chen", limit=4)
    unknown = adapter.recommend("new-user", limit=4)

    assert sparse and all(row["backend"] == "reference" for row in sparse)
    assert sparse[0]["backend_reason"] == "history_below_collaborative_threshold"
    assert unknown and all(row["backend"] == "reference" for row in unknown)
    assert unknown[0]["backend_reason"] == "unknown_user"


def test_explicit_bpr_override_remains_available():
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(
        catalog,
        model="bpr",
        min_history=3,
        model_kwargs={"iterations": 3, "num_threads": 1, "random_state": 42},
    )

    assert adapter.model_name == "bpr"
    assert adapter.capability_manifest()["model"] == "bpr"
    assert adapter.recommend("u-chen", limit=4)[0]["backend"] == "implicit_bpr"


def test_zero_limit_skips_collaborative_model_and_reference_fallback(monkeypatch):
    adapter = ImplicitRecommendationAdapter(
        build_sample_catalog(),
        model="bpr",
        min_history=3,
        model_kwargs={"iterations": 1, "num_threads": 1, "random_state": 42},
    )

    def should_not_call(*args, **kwargs):
        pytest.fail("zero-limit recommend must not invoke a serving backend")

    monkeypatch.setattr(adapter.model, "recommend", should_not_call)
    monkeypatch.setattr(adapter.fallback, "recommend", should_not_call)

    assert adapter.recommend("u-chen", limit=0) == []
    assert adapter.recommend("new-user", limit=-2) == []


@pytest.mark.parametrize("raw", [1.5, "2", True])
def test_invalid_limit_fails_before_collaborative_model_or_reference_fallback(monkeypatch, raw):
    adapter = ImplicitRecommendationAdapter(
        build_sample_catalog(),
        model="bpr",
        min_history=3,
        model_kwargs={"iterations": 1, "num_threads": 1, "random_state": 42},
    )

    def should_not_call(*args, **kwargs):
        pytest.fail("invalid-limit recommend must fail before a serving backend is invoked")

    monkeypatch.setattr(adapter.model, "recommend", should_not_call)
    monkeypatch.setattr(adapter.fallback, "recommend", should_not_call)

    with pytest.raises(ValueError, match="limit must be an integer"):
        adapter.recommend("u-chen", limit=raw)  # type: ignore[arg-type]


def test_invalid_collaborative_scores_are_dropped_and_reference_fills_slate(monkeypatch):
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(
        catalog,
        model="bpr",
        min_history=3,
        model_kwargs={"iterations": 1, "num_threads": 1, "random_state": 42},
    )
    candidate_ids = ["p01", "p02", "p03"]
    candidate_indices = [adapter.item_index[item_id] for item_id in candidate_ids]

    def bad_scores(*args, **kwargs):
        return (
            adapter._np.asarray(candidate_indices),
            adapter._np.asarray([float("nan"), "not-a-score", 0.75], dtype=object),
        )

    monkeypatch.setattr(adapter.model, "recommend", bad_scores)
    results = adapter.recommend("u-chen", limit=3)

    assert len(results) == 3
    collaborative = [row for row in results if row["backend"] == "implicit_bpr"]
    reference_fill = [row for row in results if row["backend"] == "reference_fill"]
    assert [row["id"] for row in collaborative] == ["p03"]
    assert len(reference_fill) == 2
    assert all(isfinite(float(row["score"])) for row in results)
    assert len({row["id"] for row in results}) == 3


def test_non_finite_collaborative_scores_never_escape_serving(monkeypatch):
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(
        catalog,
        model="bpr",
        min_history=3,
        model_kwargs={"iterations": 1, "num_threads": 1, "random_state": 42},
    )
    candidate_indices = [adapter.item_index[item_id] for item_id in ("p01", "p02", "p03")]

    def non_finite(*args, **kwargs):
        return (
            adapter._np.asarray(candidate_indices),
            adapter._np.asarray([float("inf"), float("-inf"), float("nan")]),
        )

    monkeypatch.setattr(adapter.model, "recommend", non_finite)
    results = adapter.recommend("u-chen", limit=3)

    assert len(results) == 3
    assert all(row["backend"] == "reference_fill" for row in results)
    assert all(isfinite(float(row["score"])) for row in results)


def test_adapter_rejects_unknown_model():
    catalog = build_sample_catalog()
    with pytest.raises(ValueError, match="unknown implicit recommendation model"):
        ImplicitRecommendationAdapter(catalog, model="mystery")
