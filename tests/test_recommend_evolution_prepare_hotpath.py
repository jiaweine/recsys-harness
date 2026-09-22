from __future__ import annotations

from dataclasses import replace

import lingjing_harness.algorithms.evolution_core as core
from lingjing_harness.algorithms import RecommendationEngine
from lingjing_harness.algorithms.capabilities import CAPABILITIES, CapabilitySpec
from lingjing_harness.domain import Catalog, Interaction, Item


def _fixture(*, items: int = 360, users: int = 6) -> tuple[Catalog, RecommendationEngine, list[str]]:
    rows = [
        Item(
            item_id=f"item-{index:05d}",
            title=f"Item {index}",
            text="recommend evolution prepare cache",
            categories=[f"cat-{index % 13}", f"cluster-{index % 23}"],
            popularity=float(items - index),
            quality=((index * 13) % 1000) / 1000.0,
            freshness=((index * 29) % 1000) / 1000.0,
        )
        for index in range(items)
    ]
    interactions: list[Interaction] = []
    timestamp = 1.0
    user_ids = [f"user-{index:02d}" for index in range(users)]
    for user_index, user_id in enumerate(user_ids):
        for offset in range(24):
            interactions.append(
                Interaction(
                    user_id=user_id,
                    item_id=rows[(user_index * 31 + offset) % items].item_id,
                    weight=1.0 + (offset % 3) * 0.1,
                    timestamp=timestamp,
                )
            )
            timestamp += 1.0
    catalog = Catalog(items=rows, interactions=interactions)
    return catalog, RecommendationEngine(catalog), user_ids


def test_default_preparation_signature_tracks_only_prepare_capabilities() -> None:
    _, engine, _ = _fixture()

    assert engine.preparation_signature() == (
        engine.config.profile_strategy,
        engine.config.candidate_strategy,
        engine.config.exploration_strategy,
        engine.config.cold_start_strategy,
    )

    reranked = engine.with_config(
        replace(
            engine.config,
            profile=0.30,
            graph=0.24,
            diversity=0.18,
            rerank_strategy="semantic_mmr",
        )
    )
    assert reranked.preparation_signature() == engine.preparation_signature()

    structural = engine.with_config(
        replace(engine.config, profile_strategy="recent_intent")
    )
    assert structural.preparation_signature() != engine.preparation_signature()


def test_external_prepare_capability_disables_cross_config_cache(monkeypatch) -> None:
    _, engine, _ = _fixture()
    original = CAPABILITIES.resolve

    def external_profile(*args, **kwargs):
        return None

    external_spec = CapabilitySpec(
        group="recommend.profile",
        name=engine.config.profile_strategy,
        description="external test",
        handler=external_profile,
    )

    def resolve(group, name):
        if group == "recommend.profile":
            return external_spec
        return original(group, name)

    monkeypatch.setattr(CAPABILITIES, "resolve", resolve)

    assert engine.preparation_signature() is None


def test_cached_audits_match_uncached_exactly_and_reuse_prepare(monkeypatch) -> None:
    catalog, engine, users = _fixture()
    configs = [
        engine.config,
        replace(engine.config, diversity=0.18),
        replace(engine.config, profile=0.30, graph=0.24),
        replace(engine.config, rerank_strategy="semantic_mmr"),
    ]

    legacy = [
        core._audit_recommend_config(
            catalog,
            engine,
            users,
            config,
            slice_key="discovery",
        )
        for config in configs
    ]

    calls = 0
    original_prepare = RecommendationEngine.prepare

    def tracking_prepare(self, user_id):
        nonlocal calls
        calls += 1
        return original_prepare(self, user_id)

    monkeypatch.setattr(RecommendationEngine, "prepare", tracking_prepare)
    prepared_cache: dict[tuple[str, tuple[str, ...]], list[dict]] = {}
    optimized = [
        core._audit_recommend_config(
            catalog,
            engine,
            users,
            config,
            slice_key="discovery",
            prepared_cache=prepared_cache,
        )
        for config in configs
    ]

    assert optimized == legacy
    assert len(prepared_cache) == len(users)
    assert calls == len(users) + 3 * len(configs)


def test_structural_change_gets_separate_prepared_rows() -> None:
    catalog, engine, users = _fixture()
    cache: dict[tuple[str, tuple[str, ...]], list[dict]] = {}

    core._audit_recommend_config(
        catalog,
        engine,
        users,
        engine.config,
        slice_key="discovery",
        prepared_cache=cache,
    )
    core._audit_recommend_config(
        catalog,
        engine,
        users,
        replace(engine.config, profile_strategy="recent_intent"),
        slice_key="discovery",
        prepared_cache=cache,
    )

    assert len(cache) == 2 * len(users)
