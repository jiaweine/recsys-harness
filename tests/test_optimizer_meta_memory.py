from concurrent.futures import ThreadPoolExecutor

import pytest

from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory


def _record(sidecar: OptimizerMetaMemory, *, event_key: str, utility: float = 0.8):
    return sidecar.record(
        "catalog-key",
        "search",
        context_key="context-a",
        backend="optuna",
        context={
            "surface": "search",
            "evidence_route": "proxy",
            "budget_bucket": "small",
            "continuous_dimensions": 2,
            "categorical_bucket": "small",
            "objective_count": 2,
            "constraint_count": 2,
        },
        utility=utility,
        objective_gain=0.04,
        evaluator_calls=8,
        wall_seconds=0.2,
        event_key=event_key,
    )


def test_optimizer_meta_credit_is_idempotent(tmp_path):
    memory = AgentMemory(tmp_path / "memory.db")
    sidecar = OptimizerMetaMemory(memory)

    first = _record(sidecar, event_key="run-1")
    duplicate = _record(sidecar, event_key="run-1")
    rows = sidecar.read("catalog-key", "search")

    assert first["recorded"] is True
    assert duplicate["recorded"] is False
    assert duplicate["deduplicated"] is True
    assert len(rows) == 1
    assert rows[0]["trials"] == 1
    assert rows[0]["utility_sum"] == pytest.approx(0.8)


def test_optimizer_meta_credit_aggregates_independent_evidence(tmp_path):
    memory = AgentMemory(tmp_path / "memory.db")
    sidecar = OptimizerMetaMemory(memory)

    _record(sidecar, event_key="run-1", utility=0.8)
    _record(sidecar, event_key="run-2", utility=0.4)
    row = sidecar.read("catalog-key", "search")[0]

    assert row["trials"] == 2
    assert row["utility_sum"] == pytest.approx(1.2)
    assert row["mean_utility"] == pytest.approx(0.6)
    assert row["evaluator_calls_sum"] == 16
    assert row["context"]["evidence_route"] == "proxy"


def test_optimizer_meta_credit_survives_memory_reopen(tmp_path):
    path = tmp_path / "memory.db"
    first_memory = AgentMemory(path)
    _record(OptimizerMetaMemory(first_memory), event_key="run-1")

    second_memory = AgentMemory(path)
    rows = OptimizerMetaMemory(second_memory).read("catalog-key", "search")

    assert len(rows) == 1
    assert rows[0]["backend"] == "optuna"
    assert rows[0]["context_key"] == "context-a"
    assert rows[0]["trials"] == 1


def test_optimizer_meta_sidecar_uses_shared_in_memory_connection():
    memory = AgentMemory(":memory:")
    first = OptimizerMetaMemory(memory)
    second = OptimizerMetaMemory(memory)

    _record(first, event_key="run-1")

    assert second.read("catalog-key", "search")[0]["trials"] == 1


def test_optimizer_meta_credit_serializes_independent_store_instances(tmp_path):
    path = tmp_path / "memory.db"
    bootstrap = OptimizerMetaMemory(AgentMemory(path))
    del bootstrap
    sidecars = [OptimizerMetaMemory(AgentMemory(path)) for _ in range(8)]

    def worker(index: int):
        return _record(
            sidecars[index % len(sidecars)],
            event_key=f"parallel-{index}",
            utility=0.5,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(64)))

    assert all(row["recorded"] for row in results)
    row = OptimizerMetaMemory(AgentMemory(path)).read("catalog-key", "search")[0]
    assert row["trials"] == 64
    assert row["utility_sum"] == pytest.approx(32.0)
    assert row["evaluator_calls_sum"] == 64 * 8


def test_optimizer_meta_concurrent_duplicate_event_counts_once(tmp_path):
    path = tmp_path / "memory.db"
    sidecars = [OptimizerMetaMemory(AgentMemory(path)) for _ in range(8)]

    def worker(index: int):
        return _record(sidecars[index], event_key="same-event", utility=0.7)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(worker, range(8)))

    assert sum(bool(row["recorded"]) for row in results) == 1
    row = OptimizerMetaMemory(AgentMemory(path)).read("catalog-key", "search")[0]
    assert row["trials"] == 1
    assert row["utility_sum"] == pytest.approx(0.7)


def test_optimizer_meta_credit_rejects_non_finite_or_coerced_evidence(tmp_path):
    sidecar = OptimizerMetaMemory(AgentMemory(tmp_path / "memory.db"))
    base = {
        "catalog_key": "catalog-key",
        "domain": "search",
        "context_key": "context-a",
        "backend": "native",
        "context": {},
        "utility": 0.5,
        "objective_gain": 0.01,
        "evaluator_calls": 2,
        "wall_seconds": 0.1,
    }

    with pytest.raises(ValueError, match="utility"):
        sidecar.record(**{**base, "utility": float("nan")})
    with pytest.raises(ValueError, match="objective gain"):
        sidecar.record(**{**base, "objective_gain": float("inf")})
    with pytest.raises(ValueError, match="integer, not boolean"):
        sidecar.record(**{**base, "evaluator_calls": True})
