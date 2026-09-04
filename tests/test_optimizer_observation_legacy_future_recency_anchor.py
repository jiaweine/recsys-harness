from __future__ import annotations

from dataclasses import asdict
import json

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0
BASIS = "search_discovery_robustness_guardrails"


def _runtime_configs(registry, count=4):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    seen = set()
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            marker = repr(sorted(config.items()))
            if marker in seen:
                continue
            seen.add(marker)
            configs.append(config)
            if len(configs) >= count:
                return configs
    assert len(configs) >= count
    return configs


def _insert_legacy_latest_rows(memory, catalog_key, configs, updated_at):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    observation_memory._ensure_optimizer_observation_table(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("begin immediate")
            for index, config in enumerate(configs):
                config_json = json.dumps(
                    config,
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
                connection.execute(
                    """
                    insert into agent_optimizer_observations(
                      catalog_key,domain,config_key,config,score,feasible,source,generation,
                      feasibility_basis,constraints,seen_count,created_at,updated_at
                    ) values(?,?,?,?,?,?,?,?,?,?,1,?,?)
                    on conflict(catalog_key,domain,config_key) do update set
                      config=excluded.config,
                      score=excluded.score,
                      feasible=excluded.feasible,
                      source=excluded.source,
                      generation=excluded.generation,
                      feasibility_basis=excluded.feasibility_basis,
                      constraints=excluded.constraints,
                      updated_at=excluded.updated_at
                    """,
                    (
                        catalog_key,
                        "search",
                        observation_memory._config_key(config),
                        config_json,
                        0.2 + 0.1 * index,
                        1 if index % 2 == 0 else 0,
                        "legacy_future_observation_recency_contract",
                        index,
                        BASIS,
                        json.dumps(
                            {"worse_share": 0.1, "worst_delta": -0.1},
                            sort_keys=True,
                        ),
                        updated_at,
                        updated_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            memory._close(connection)


def _insert_stale_history_rows(memory, catalog_key, configs, observed_at):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    observation_memory._ensure_optimizer_observation_table(memory)
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("begin immediate")
            for index, config in enumerate(configs):
                connection.execute(
                    """
                    insert into agent_optimizer_observation_history(
                      catalog_key,domain,config_key,config,score,feasible,source,generation,
                      feasibility_basis,constraints,observed_at
                    ) values(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        catalog_key,
                        "search",
                        observation_memory._config_key(config),
                        json.dumps(
                            config,
                            ensure_ascii=False,
                            sort_keys=True,
                            allow_nan=False,
                        ),
                        -0.4 - 0.1 * index,
                        0 if index % 2 == 0 else 1,
                        "stale_pre_migration_history",
                        index + 100,
                        BASIS,
                        json.dumps(
                            {"worse_share": 0.4, "worst_delta": -0.4},
                            sort_keys=True,
                        ),
                        observed_at,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            memory._close(connection)


def _history_count(memory):
    with memory._lock:
        connection = memory._connect()
        try:
            row = connection.execute(
                "select count(*) as count from agent_optimizer_observation_history"
            ).fetchone()
        finally:
            memory._close(connection)
    return int(row["count"])


def test_legacy_latest_only_future_rows_age_from_first_local_view_across_restarts(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "legacy-future-observation-recency-anchor.db"
    clock = {"now": 120_000_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(registry)
    local_first_seen_at = clock["now"]
    writer_future = local_first_seen_at + 365.0 * DAY

    # Simulate a database created before paid-observation history IDs existed:
    # current durable rows are present, but there are no history commit rows that
    # the modern recency anchor can use as an identity.
    _insert_legacy_latest_rows(
        registry.memory,
        registry.catalog_key,
        configs,
        writer_future,
    )
    assert _history_count(registry.memory) == 0

    raw = registry.memory.optimizer_observations(registry.catalog_key, "search")
    assert len(raw) == 4
    assert {row["updated_at"] for row in raw} == {writer_future}

    clock["now"] = local_first_seen_at
    first = registry._routing_context("search")
    assert first.landscape.informative is True

    # The same legacy durable rows must age on the caller timeline instead of
    # receiving a fresh age=0 clamp until the old writer clock catches up.
    clock["now"] = local_first_seen_at + 56.0 * DAY
    aged_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    aged = aged_registry._routing_context("search")
    assert aged.landscape.informative is False

    # Compatibility repair is routing metadata only: do not manufacture paid
    # history or rewrite the durable observation timestamps.
    assert _history_count(aged_registry.memory) == 0
    raw_after = aged_registry.memory.optimizer_observations(
        aged_registry.catalog_key,
        "search",
    )
    assert {row["updated_at"] for row in raw_after} == {writer_future}


def test_legacy_future_latest_does_not_borrow_stale_history_commit_identity(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "partial-history-future-observation-recency-anchor.db"
    clock = {"now": 130_000_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(registry)
    local_first_seen_at = clock["now"]
    stale_history_at = local_first_seen_at - 90.0 * DAY
    writer_future = local_first_seen_at + 365.0 * DAY

    # A partially migrated database can contain an old history version for a
    # config and a newer latest-table-only version. The old history ID must not be
    # treated as the commit identity of the newer latest row merely because the
    # config_key matches.
    _insert_stale_history_rows(
        registry.memory,
        registry.catalog_key,
        configs,
        stale_history_at,
    )
    _insert_legacy_latest_rows(
        registry.memory,
        registry.catalog_key,
        configs,
        writer_future,
    )
    assert _history_count(registry.memory) == 4

    clock["now"] = local_first_seen_at
    first = registry._routing_context("search")
    assert first.landscape.informative is True

    clock["now"] = local_first_seen_at + 56.0 * DAY
    aged_registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    aged = aged_registry._routing_context("search")
    assert aged.landscape.informative is False

    # Still no repair of paid history or durable latest clocks: compatibility is
    # restricted to the routing snapshot and its separate anchor metadata.
    assert _history_count(aged_registry.memory) == 4
    raw_after = aged_registry.memory.optimizer_observations(
        aged_registry.catalog_key,
        "search",
    )
    assert {row["updated_at"] for row in raw_after} == {writer_future}
