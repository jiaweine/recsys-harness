from __future__ import annotations

from dataclasses import replace

from lingjing_harness.algorithms import (
    RecommendConfig,
    RecommendationEngine,
    audit_recommend,
    prepare_recommend_relevance,
)
from lingjing_harness.integrations import ImplicitHybridRecommendationEngine
from lingjing_harness.runtime import RecommendationBackendToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


class _FakeCollaborativeAdapter:
    def __init__(
        self,
        catalog,
        *,
        model="als",
        min_history=1,
        model_kwargs=None,
        fallback=None,
        **_,
    ):
        self.catalog = catalog
        self.model_name = model
        self.min_history = min_history
        self.model_kwargs = dict(model_kwargs or {})
        self.fallback = fallback

    def capability_manifest(self):
        return {
            "backend": "implicit",
            "model": self.model_name,
            "min_history": self.min_history,
            "training_users": len({row.user_id for row in self.catalog.interactions}),
            "training_items": len(self.catalog.items),
            "training_interactions": len(self.catalog.interactions),
            "fallback": "reference",
        }

    def recommend(self, user_id, *, limit=10):
        history = [row for row in self.catalog.interactions if row.user_id == user_id]
        if len(history) < self.min_history:
            return []
        seen = {row.item_id for row in history}
        candidates = [
            item
            for item in self.catalog.items
            if item.eligible and item.item_id not in seen
        ]
        # Reverse catalog order so the collaborative signal is observably
        # different from the owned reference ranking.
        candidates.reverse()
        return [
            {
                **item.public_dict(),
                "score": float(len(candidates) - index),
                "backend": f"implicit_{self.model_name}",
            }
            for index, item in enumerate(candidates[:limit])
        ]


def _hybrid(catalog, *, config=None):
    reference = RecommendationEngine(catalog, config or RecommendConfig())
    adapter = _FakeCollaborativeAdapter(catalog, fallback=reference)
    return ImplicitHybridRecommendationEngine(
        reference,
        adapter,
        collaborative_limit=12,
        adapter_options={"model": "als", "min_history": 1},
    )


def _use_fake_temporal_adapter(monkeypatch):
    import lingjing_harness.integrations.implicit_hybrid as implicit_hybrid

    monkeypatch.setattr(
        implicit_hybrid,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )


def test_hybrid_collaborative_signal_flows_through_harness_ranking_contract():
    catalog = build_sample_catalog()
    config = replace(RecommendConfig(), graph=0.75)
    engine = _hybrid(catalog, config=config)

    prepared = engine.prepare("u-lin")
    collaborative = [row for row in prepared if row.get("_collaborative", 0.0) > 0.0]
    assert collaborative
    assert all(row["graph"] == row["_collaborative"] for row in collaborative)

    results = engine.recommend("u-lin", limit=6)
    assert results
    assert all(row["backend"] == "hybrid_implicit_als" for row in results)
    assert all("novelty" in row["signals"] for row in results)
    assert all("collaborative" in row["signals"] for row in results)
    assert any(row["signals"]["collaborative"] > 0 for row in results)


def test_sparse_user_keeps_exact_owned_reference_path():
    catalog = build_sample_catalog()
    reference = RecommendationEngine(catalog)
    adapter = _FakeCollaborativeAdapter(catalog, min_history=99, fallback=reference)
    hybrid = ImplicitHybridRecommendationEngine(reference, adapter)

    assert hybrid.recommend("u-lin", limit=6) == reference.recommend("u-lin", limit=6)
    assert hybrid.recommend("brand-new-user", limit=6) == reference.recommend("brand-new-user", limit=6)


def test_with_config_reuses_collaborative_state_while_changing_harness_strategy():
    catalog = build_sample_catalog()
    engine = _hybrid(catalog)
    changed = engine.with_config(replace(engine.config, diversity=0.28))

    assert changed.adapter is engine.adapter
    assert changed.reference is not engine.reference
    assert changed.config.diversity == 0.28
    assert changed._by_user is changed.reference._by_user
    assert changed.recommend("u-lin", limit=4)


def test_domain_audit_runs_on_the_hybrid_runtime(monkeypatch):
    catalog = build_sample_catalog()
    _use_fake_temporal_adapter(monkeypatch)
    report = audit_recommend(catalog, _hybrid(catalog))

    assert report["users"] >= 3
    assert report["coverage"] > 0
    assert report["cold_start_samples"] == 3


def test_temporal_relevance_rebuilds_same_backend_on_point_in_time_catalogs(monkeypatch):
    catalog = build_sample_catalog()
    engine = _hybrid(catalog)
    _use_fake_temporal_adapter(monkeypatch)
    prepared = prepare_recommend_relevance(catalog, engine, k=5)

    assert prepared.slices
    assert all(isinstance(row.engine, ImplicitHybridRecommendationEngine) for row in prepared.slices)
    assert all(row.engine.catalog is not catalog for row in prepared.slices)
    assert all(
        len(row.engine.catalog.interactions) < len(catalog.interactions)
        for row in prepared.slices
    )
    assert prepared.evaluate(engine.config)["available"] is True


def test_backend_registry_is_explicit_and_composes_with_existing_runtime(monkeypatch):
    catalog = build_sample_catalog()

    import lingjing_harness.runtime.collaborative_tools as collaborative_tools

    monkeypatch.setattr(
        collaborative_tools,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )

    reference = RecommendationBackendToolRegistry(catalog)
    assert not isinstance(reference.recommend, ImplicitHybridRecommendationEngine)
    assert reference.inspect_data()["recommend_backend"]["backend"] == "reference"

    hybrid = RecommendationBackendToolRegistry(
        catalog,
        recommend_backend="implicit_als",
        recommend_backend_kwargs={"min_history": 1, "collaborative_limit": 12},
    )
    assert isinstance(hybrid.recommend, ImplicitHybridRecommendationEngine)
    assert hybrid.inspect_data()["recommend_backend"]["mode"] == "hybrid_collaborative_signal"
    assert hybrid.run_recommend("u-lin")["results"][0]["backend"] == "hybrid_implicit_als"

    clone = hybrid.fork()
    assert isinstance(clone.recommend, ImplicitHybridRecommendationEngine)
    assert clone.recommend.adapter is hybrid.recommend.adapter
    assert clone.recommend_backend == "implicit_als"


def test_invalid_backend_options_fail_closed_before_runtime_install(monkeypatch):
    catalog = build_sample_catalog()

    import lingjing_harness.runtime.collaborative_tools as collaborative_tools

    monkeypatch.setattr(
        collaborative_tools,
        "ImplicitRecommendationAdapter",
        _FakeCollaborativeAdapter,
    )

    try:
        RecommendationBackendToolRegistry(catalog, recommend_backend="mystery")
    except ValueError as exc:
        assert "unknown recommendation backend" in str(exc)
    else:
        raise AssertionError("unknown backend must be rejected")

    try:
        RecommendationBackendToolRegistry(
            catalog,
            recommend_backend="implicit_als",
            recommend_backend_kwargs={"model": "bpr"},
        )
    except ValueError as exc:
        assert "requires model='als'" in str(exc)
    else:
        raise AssertionError("implicit_als must not silently select another model")
