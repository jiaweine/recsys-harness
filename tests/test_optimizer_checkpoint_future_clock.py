from __future__ import annotations

from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)


DAY = 24.0 * 60.0 * 60.0


def _store(tmp_path, name: str):
    memory = AgentMemory(tmp_path / name)
    return memory, OptimizerRoutingCheckpointStore(OptimizerMetaMemory(memory))


def test_checkpoint_cannot_persist_evidence_ahead_of_decision_clock(tmp_path):
    _, store = _store(tmp_path, "future-checkpoint-write.db")
    now = 10_000.0
    future = now + 365.0 * DAY

    checkpoint = store.record(
        "catalog",
        "search",
        regime="weighted",
        evidence_updated_at=future,
        evidence_seen_count=4,
        evidence_rows=4,
        epoch_started_at=future,
        decision_at=now,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    assert checkpoint["recorded"] is True
    assert checkpoint["evidence_updated_at"] == now
    assert checkpoint["epoch_started_at"] == now
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["decision_at"] == now


def test_normal_decision_repairs_legacy_future_skewed_checkpoint(tmp_path):
    memory, store = _store(tmp_path, "future-checkpoint-repair.db")
    now = 20_000.0
    future = now + 365.0 * DAY
    scoped_catalog_key = store._scoped_catalog_key("catalog", "search")

    # Simulate a checkpoint persisted by the pre-fix runtime, where durable
    # observation wall clock was incorrectly allowed to lead the caller clock.
    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute(
                """
                insert into agent_optimizer_routing_checkpoint(
                  catalog_key,domain,regime,evidence_updated_at,evidence_seen_count,
                  evidence_rows,evidence_epoch,epoch_started_at,decision_at,expires_at
                ) values(?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scoped_catalog_key,
                    "search",
                    "weighted",
                    future,
                    4,
                    4,
                    1,
                    future,
                    now,
                    now + OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
                ),
            )
            connection.commit()
        finally:
            memory._close(connection)

    repaired = store.record(
        "catalog",
        "search",
        regime="fallback",
        evidence_updated_at=now + 100.0,
        evidence_seen_count=8,
        evidence_rows=4,
        epoch_started_at=now + 100.0,
        decision_at=now + 100.0,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )

    assert repaired["recorded"] is True
    assert repaired["regime"] == "fallback"
    assert repaired["evidence_updated_at"] == now + 100.0
    assert repaired["evidence_seen_count"] == 8
    assert repaired["epoch_started_at"] == now + 100.0
    assert repaired["evidence_epoch"] == 1
    assert repaired["decision_at"] == now + 100.0
