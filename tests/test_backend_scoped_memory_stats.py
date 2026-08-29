from __future__ import annotations

from dataclasses import asdict, replace

from lingjing_harness.algorithms import RecommendConfig, SearchConfig
from lingjing_harness.algorithms.segments import strategy_domain
from lingjing_harness.runtime import AgentHarness, ToolRegistry
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.memory import AgentMemory, catalog_fingerprint
from lingjing_harness.sample_data import build_sample_catalog


def _remember(memory, catalog_key: str, domain: str, config, *, status: str) -> None:
    memory.remember_strategy(
        catalog_key,
        domain,
        asdict(config),
        score=0.8,
        evidence=8,
        status=status,
    )


def test_stats_keep_shared_episodes_but_only_current_backend_strategy_state() -> None:
    catalog = build_sample_catalog()
    key = catalog_fingerprint(catalog)
    base = AgentMemory()
    base.record_episode(
        key,
        "audit workspace",
        "audit",
        0.9,
        findings=["ok"],
        action_keys=["data.inspect"],
        learned=[],
    )

    # Historical reference strategies remain durable, but only the search surface
    # is reference in the runtime under test. The old reference recommendation
    # strategy/credit must not be reported as part of the active ALS namespace.
    _remember(base, key, "search", replace(SearchConfig(), diversity=0.11), status="active")
    _remember(base, key, "recommend", replace(RecommendConfig(), diversity=0.12), status="active")
    base.record_strategy_credit(
        key,
        "search",
        "reference-search-arm",
        outcome="accepted",
        event_key="reference-search-credit",
    )
    base.record_strategy_credit(
        key,
        "recommend",
        "reference-recommend-arm",
        outcome="accepted",
        event_key="reference-recommend-credit",
    )

    scoped = BackendScopedMemory(
        base,
        search_scope="",
        recommend_scope="recommend-als-test",
        invocation_scope="runtime-test",
    )
    _remember(
        scoped,
        key,
        strategy_domain("search", "search/general"),
        replace(SearchConfig(), diversity=0.21),
        status="trusted",
    )
    _remember(
        scoped,
        key,
        "recommend",
        replace(RecommendConfig(), diversity=0.31),
        status="active",
    )
    _remember(
        scoped,
        key,
        strategy_domain("recommend", "recommend/warm"),
        replace(RecommendConfig(), diversity=0.32),
        status="trusted",
    )
    scoped.record_strategy_credit(
        key,
        "recommend",
        "als-recommend-arm",
        outcome="rejected",
        event_key="als-recommend-credit",
    )

    base_stats = base.stats(key)
    assert base_stats["episodes"] == 1
    assert base_stats["skills"] == 3
    assert base_stats["active_strategies"] == 2
    assert base_stats["credit_arms"] == 2
    assert base_stats["negative_credit_arms"] == 0
    assert base_stats["credit_events"] == 2

    assert scoped.stats(key) == {
        "episodes": 1,
        "skills": 4,
        "active_strategies": 2,
        "credit_arms": 2,
        "negative_credit_arms": 1,
        "credit_events": 2,
    }


def test_public_harness_uses_the_tool_memory_facade(monkeypatch) -> None:
    import lingjing_harness.runtime as runtime_module

    catalog = build_sample_catalog()
    base = AgentMemory()
    scoped = BackendScopedMemory(base, recommend_scope="recommend-test")
    tools = ToolRegistry(catalog, scoped)

    monkeypatch.setattr(
        runtime_module,
        "build_runtime_tools",
        lambda catalog, memory, config=None: tools,
    )
    harness = AgentHarness(catalog, memory=base)

    assert harness.tools is tools
    assert harness.tools.memory is scoped
    assert harness.memory is scoped


def test_unscoped_global_stats_remain_a_base_memory_diagnostic() -> None:
    base = AgentMemory()
    scoped = BackendScopedMemory(base, search_scope="search-test")

    assert scoped.stats() == base.stats()
