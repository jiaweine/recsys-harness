from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict

import pytest

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.backend_memory import BackendScopedMemory
from lingjing_harness.runtime.optimizer_observation_drift import (
    detect_optimizer_observation_drift,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _dimension():
    return core.EvolutionDimension(
        name="x",
        kind="continuous",
        group="independent",
        low=0.0,
        high=1.0,
    )


def _latest_rows(feasible):
    return [
        {
            "config": {"x": x},
            "objective": score,
            "feasible": label,
            "updated_at": 2_000.0,
            "seen_count": 2,
        }
        for x, score, label in zip(
            (0.1, 0.3, 0.6, 0.9),
            (0.2, 0.4, 0.6, 0.8),
            feasible,
        )
    ]


def _history_rows(recent_feasible, history_feasible, *, history_basis=BASIS):
    rows = []
    for index, label in enumerate(recent_feasible):
        rows.append(
            {
                "config_key": f"config-{index}",
                "config": {"x": (0.1, 0.3, 0.6, 0.9)[index]},
                "objective": (0.2, 0.4, 0.6, 0.8)[index],
                "feasible": label,
                "feasibility_basis": BASIS,
                "observed_at": 2_000.0,
            }
        )
    for index, label in enumerate(history_feasible):
        rows.append(
            {
                "config_key": f"config-{index}",
                "config": {"x": (0.1, 0.3, 0.6, 0.9)[index]},
                "objective": (0.2, 0.4, 0.6, 0.8)[index],
                "feasible": label,
                "feasibility_basis": history_basis,
                "observed_at": 1_000.0,
            }
        )
    return rows


def _runtime_observations(registry, feasible):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            configs.append(config)
            if len(configs) >= 4:
                break
        if len(configs) >= 4:
            break
    assert len(configs) == 4
    return [
        {
            "config": config,
            "objective": 0.2 + 0.15 * index,
            "feasible": label,
            "source": "history_contract_evaluator",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, (config, label) in enumerate(zip(configs, feasible))
    ]


def test_same_config_feasibility_history_can_detect_change_with_only_four_latest_rows():
    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=_latest_rows([True, True, True, True]),
        observation_history=_history_rows(
            [True, True, True, True],
            [False, False, True, True],
        ),
    )

    assert result["change_detected"] is True
    assert result["reason"] == "change_detected"
    assert result["primary_signals"] == ["same_config_feasibility_shift"]
    assert result["same_config_feasibility_history_available"] is True
    assert result["same_config_feasibility_pairs"] == 4
    assert result["same_config_feasibility_flips"] == 2
    assert result["same_config_feasibility_flip_rate"] == pytest.approx(0.5)
    assert result["recent_rows"] == 4
    assert result["usable_rows"] == 4
    assert result["new_evaluator_calls"] == 0


def test_same_config_feasibility_history_requires_matching_guardrail_semantics():
    result = detect_optimizer_observation_drift(
        dimensions=[_dimension()],
        observations=_latest_rows([True, True, True, True]),
        observation_history=_history_rows(
            [True, True, True, True],
            [False, False, False, False],
            history_basis="different_constraint_contract",
        ),
    )

    assert result["change_detected"] is False
    assert result["reason"] == "insufficient_rows"
    assert result["same_config_feasibility_history_available"] is False
    assert result["same_config_feasibility_pairs"] == 0
    assert result["same_config_feasibility_basis_mismatches"] == 4


def test_durable_observation_history_preserves_prior_labels_without_changing_latest_upsert(
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    memory = AgentMemory()
    clock = {"now": 1_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    old_rows = [
        {
            "config": {"x": x},
            "objective": score,
            "feasible": label,
            "source": "paid_history_test",
            "generation": index,
            "feasibility_basis": BASIS,
        }
        for index, (x, score, label) in enumerate(
            zip(
                (0.1, 0.3, 0.6, 0.9),
                (0.2, 0.4, 0.6, 0.8),
                (False, False, True, True),
            )
        )
    ]
    new_rows = [
        {**row, "feasible": True, "generation": int(row["generation"]) + 10}
        for row in old_rows
    ]

    first = memory.record_optimizer_observations("catalog", "search", old_rows)
    clock["now"] = 2_000.0
    second = memory.record_optimizer_observations("catalog", "search", new_rows)
    latest = memory.optimizer_observations("catalog", "search")
    history = memory.optimizer_observation_history("catalog", "search")

    assert first["history_rows"] == 4
    assert second["history_rows"] == 4
    assert second["updated_rows"] == 4
    assert len(latest) == 4
    assert {row["seen_count"] for row in latest} == {2}
    assert {row["feasible"] for row in latest} == {True}
    assert len(history) == 8
    assert {row["observed_at"] for row in history} == {1_000.0, 2_000.0}
    assert sum(1 for row in history if not row["feasible"]) == 2


def test_durable_history_and_latest_upsert_survive_multi_connection_contention(tmp_path):
    path = tmp_path / "optimizer-history.db"
    memories = [AgentMemory(path) for _ in range(8)]
    template = [
        {
            "config": {"x": x},
            "objective": score,
            "feasible": index >= 2,
            "source": "paid_contention_test",
            "generation": index,
            "feasibility_basis": BASIS,
        }
        for index, (x, score) in enumerate(
            zip((0.1, 0.3, 0.6, 0.9), (0.2, 0.4, 0.6, 0.8))
        )
    ]

    def write(worker_index):
        rows = [
            {
                **row,
                "objective": float(row["objective"]) + worker_index * 0.001,
                "feasible": bool((worker_index + index) % 2),
            }
            for index, row in enumerate(template)
        ]
        return memories[worker_index].record_optimizer_observations(
            "catalog",
            "search",
            rows,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        summaries = list(pool.map(write, range(8)))

    reader = AgentMemory(path)
    latest = reader.optimizer_observations("catalog", "search")
    history = reader.optimizer_observation_history("catalog", "search")

    assert all(summary["captured_rows"] == 4 for summary in summaries)
    assert all(summary["history_rows"] == 4 for summary in summaries)
    assert len(latest) == 4
    assert {row["seen_count"] for row in latest} == {8}
    assert len(history) == 32
    for x in (0.1, 0.3, 0.6, 0.9):
        assert sum(1 for row in history if row["config"]["x"] == x) == 8


def test_same_config_history_is_backend_scoped_with_latest_observations(monkeypatch):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    monkeypatch.setattr(observation_memory.time, "time", lambda: 3_000.0)
    base = AgentMemory()
    scoped = BackendScopedMemory(base, search_scope="semantic-history-a")
    row = {
        "config": {"x": 0.25},
        "objective": 0.5,
        "feasible": False,
        "source": "paid_history_test",
        "generation": 0,
        "feasibility_basis": BASIS,
    }

    scoped.record_optimizer_observations("catalog", "search", [row])

    assert len(scoped.optimizer_observations("catalog", "search")) == 1
    assert len(scoped.optimizer_observation_history("catalog", "search")) == 1
    assert base.optimizer_observations("catalog", "search") == []
    assert base.optimizer_observation_history("catalog", "search") == []


def test_runtime_uses_basis_matched_same_config_feasibility_change_as_routing_only_drift(
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    clock = {"now": 4_000.0}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _runtime_observations(registry, [False, False, True, True]),
    )
    clock["now"] = 5_000.0
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _runtime_observations(registry, [True, True, True, True]),
    )

    before = registry._routing_context_without_optimizer_observations("search")
    after = registry._routing_context("search")
    manifest = registry.inspect_data()["optimizer_meta_router"]
    state = manifest["optimizer_observation_drift_states"]["search"]

    assert state["change_detected"] is True
    assert state["action"] == "recent_only_weighted_geometry"
    assert "same_config_feasibility_shift" in state["primary_signals"]
    assert state["same_config_feasibility_pairs"] == 4
    assert state["same_config_feasibility_flip_rate"] == pytest.approx(0.5)
    assert state["recent_confidence"]["enter_confident"] is True
    assert after.landscape.informative is True
    assert after.landscape.feasible_density == pytest.approx(1.0)
    assert after.warm_start_rows == before.warm_start_rows
    assert manifest["optimizer_observation_history"] == "bounded_same_config_evaluator_history"
    assert manifest["optimizer_observation_history_retention"] == 192
    assert manifest["optimizer_observation_drift_same_config_feasibility"] == "primary_with_basis_matched_history"
    assert manifest["optimizer_observation_drift_same_config_min_pairs"] == 4
    assert manifest["optimizer_observation_drift_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_drift_evaluator_calls"] == 0
