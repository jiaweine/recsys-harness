from __future__ import annotations

from dataclasses import asdict

from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0
BASIS = "search_discovery_robustness_guardrails"


def _row(registry):
    return {
        "config": asdict(registry.search.config),
        "objective": 0.5,
        "feasible": True,
        "source": "recency_anchor_snapshot_race_contract",
        "generation": 0,
        "feasibility_basis": BASIS,
        "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
    }


def _raw_snapshot(memory, catalog_key, domain):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_snapshot as snapshot_runtime

    with memory._lock:
        connection = memory._connect()
        try:
            connection.execute("begin")
            latest_rows = observation_memory._latest_observation_rows(
                connection,
                catalog_key=catalog_key,
                domain=domain,
                limit=observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET,
            )
            history_rows = connection.execute(
                """
                select id,config_key,config,score,feasible,source,generation,
                       feasibility_basis,constraints,observed_at
                from agent_optimizer_observation_history
                where catalog_key=? and domain=?
                order by observed_at desc,id desc limit ?
                """,
                (
                    catalog_key,
                    domain,
                    observation_memory.OPTIMIZER_OBSERVATION_HISTORY_READ_BUDGET,
                ),
            ).fetchall()
            connection.commit()
        finally:
            memory._close(connection)

    observations = snapshot_runtime._decode_latest_rows(list(latest_rows))
    history = snapshot_runtime._decode_history_rows(list(history_rows))
    latest_id_by_config = {}
    history_clock_rows = []
    for row in history:
        observation_id = int(row["observation_commit_id"])
        config_key = str(row["config_key"])
        latest_id_by_config[config_key] = max(
            observation_id,
            latest_id_by_config.get(config_key, 0),
        )
        history_clock_rows.append(
            {
                "observation_id": observation_id,
                "config_key": config_key,
                "observed_at": float(row["observed_at"]),
            }
        )
    for row in observations:
        row["observation_commit_id"] = latest_id_by_config[str(row["config_key"])]
    return {
        "observations": observations,
        "history": history,
        "history_clock_rows": history_clock_rows,
        "history_rows_read": len(history),
        "history_filtered_rows": 0,
        "latest_newest_at": max(row["updated_at"] for row in observations),
        "history_newest_at": max(row["observed_at"] for row in history),
    }, int(history_rows[0]["id"])


def test_recency_anchor_keeps_snapshot_commit_identity_across_concurrent_same_config_write(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_recency_anchor as recency_anchor

    path = tmp_path / "recency-anchor-snapshot-race.db"
    local_now = 120_000_000.0
    writer_future = local_now + 365.0 * DAY
    clock = {"now": writer_future}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])

    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    paid_row = _row(registry)
    recency_anchor._ensure_anchor_table(registry.memory)

    # The first paid version is future-skewed. Capture the exact coherent routing
    # cohort before any later writer changes the same config.
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        [paid_row],
    )
    snapshot, first_commit_id = _raw_snapshot(
        registry.memory,
        registry.catalog_key,
        "search",
    )
    assert snapshot["observations"][0]["updated_at"] == writer_future
    assert snapshot["observations"][0]["observation_commit_id"] == first_commit_id

    # A normal-clock writer commits a later paid version after the snapshot
    # transaction but before the recency-anchor layer performs durable anchor I/O.
    # The old snapshot must still be interpreted using its own paid commit identity.
    clock["now"] = local_now
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        [paid_row],
    )

    normalized, _ = recency_anchor._normalized_snapshot(
        registry.memory,
        catalog_key=registry.catalog_key,
        domain="search",
        snapshot=snapshot,
        reference_time=local_now,
    )

    observation = normalized["observations"][0]
    assert observation["observation_commit_id"] == first_commit_id
    assert observation["routing_raw_updated_at"] == writer_future
    assert observation["routing_clock_anchor_at"] == local_now
    assert observation["updated_at"] == local_now
