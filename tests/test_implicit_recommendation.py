from __future__ import annotations

import pytest

pytest.importorskip("implicit")

from lingjing_harness.domain import Interaction
from lingjing_harness.integrations import ImplicitRecommendationAdapter
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


def test_sparse_and_unknown_users_use_reference_fallback():
    catalog = build_sample_catalog()
    adapter = ImplicitRecommendationAdapter(catalog, model="bpr", min_history=5, model_kwargs={"iterations": 3})

    sparse = adapter.recommend("u-chen", limit=4)
    unknown = adapter.recommend("new-user", limit=4)

    assert sparse and all(row["backend"] == "reference" for row in sparse)
    assert sparse[0]["backend_reason"] == "history_below_collaborative_threshold"
    assert unknown and all(row["backend"] == "reference" for row in unknown)
    assert unknown[0]["backend_reason"] == "unknown_user"


def test_adapter_rejects_unknown_model():
    catalog = build_sample_catalog()
    with pytest.raises(ValueError, match="unknown implicit recommendation model"):
        ImplicitRecommendationAdapter(catalog, model="mystery")
