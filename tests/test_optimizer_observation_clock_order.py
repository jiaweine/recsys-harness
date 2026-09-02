from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _wide_runtime_configs(registry, count):
    base = asdict(registry.search.config)
    return [
        {
            **base,
            "clock_order_contract_case": index,
        }
        for index in range(count)
    ]


def _rows(configs, *, offset=0.0):
    return [
        {
            "config": config,
            "objective": offset + 0.2 + 0.001 * index,
            "feasible": True,
            "source": "clock_order_contract_evaluator",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs)
    ]


def _keys(observations, observation_memory):
    return {
        observation_memory._config_key(row["config"])
        for row in observations
    }


def test_latest_retention_keeps_new_paid_config_when_writer_clock_rolls_back(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "optimizer-latest-retention-clock-rollback.db"
    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _wide_runtime_configs(
        registry,
        observation_memory.OPTIMIZER_OBSERVATION_RETENTION + 1,
    )
    original = configs[:-1]
    newest = configs[-1]

    monkeypatch.setattr(observation_memory.time, "time", lambda: 1_000.0)
    first = registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(original),
    )
    assert first["history_rows"] == observation_memory.OPTIMIZER_OBSERVATION_RETENTION
    assert first["new_evaluator_calls"] == 0

    monkeypatch.setattr(observation_memory.time, "time", lambda: 500.0)
    second = registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows([newest], offset=1.0),
    )
    assert second["history_rows"] == 1
    assert second["new_evaluator_calls"] == 0

    latest = registry.memory.optimizer_observations(
        registry.catalog_key,
        "search",
        limit=observation_memory.OPTIMIZER_OBSERVATION_RETENTION,
    )
    expected = {
        observation_memory._config_key(config)
        for config in configs[1:]
    }
    assert len(latest) == observation_memory.OPTIMIZER_OBSERVATION_RETENTION
    assert _keys(latest, observation_memory) == expected
    assert observation_memory._config_key(newest) in expected
    assert min(row["updated_at"] for row in latest) == 500.0


def test_routing_latest_budget_includes_new_paid_config_after_clock_rollback(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_snapshot as runtime_snapshot

    path = tmp_path / "optimizer-routing-latest-clock-rollback.db"
    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    read_budget = observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET
    configs = _wide_runtime_configs(registry, read_budget + 5)
    original = configs[:-1]
    newest = configs[-1]

    monkeypatch.setattr(observation_memory.time, "time", lambda: 1_000.0)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(original),
    )
    monkeypatch.setattr(observation_memory.time, "time", lambda: 500.0)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows([newest], offset=1.0),
    )

    snapshot = runtime_snapshot._read_atomic_snapshot(
        registry.memory,
        registry.catalog_key,
        "search",
    )
    expected = {
        observation_memory._config_key(config)
        for config in configs[-read_budget:]
    }
    latest_keys = {
        str(row.get("config_key") or "")
        for row in snapshot["observations"]
    }
    history_keys = {
        str(row.get("config_key") or "")
        for row in snapshot["history"]
    }

    assert len(snapshot["observations"]) == read_budget
    assert latest_keys == expected
    assert observation_memory._config_key(newest) in latest_keys
    assert history_keys <= latest_keys
    assert observation_memory._config_key(newest) in history_keys
    assert snapshot["history_rows_read"] == len(configs)
    assert snapshot["history_filtered_rows"] == len(configs) - read_budget
    assert min(row["updated_at"] for row in snapshot["observations"]) == 500.0
