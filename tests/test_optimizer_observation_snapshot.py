from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Event

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


BASIS = "search_discovery_robustness_guardrails"


def _runtime_configs(registry, count=8):
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


def _wide_runtime_configs(registry, count=52):
    base = asdict(registry.search.config)
    return [
        {
            **base,
            "snapshot_contract_case": index,
        }
        for index in range(count)
    ]


def _rows(configs, *, feasible=True, offset=0.0):
    return [
        {
            "config": config,
            "objective": offset + 0.2 + 0.05 * index,
            "feasible": bool(feasible),
            "source": "snapshot_contract_evaluator",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs)
    ]


class _FetchPauseCursor:
    def __init__(self, cursor, entered: Event, release: Event) -> None:
        self._cursor = cursor
        self._entered = entered
        self._release = release

    def fetchall(self):
        rows = self._cursor.fetchall()
        self._entered.set()
        assert self._release.wait(10.0)
        return rows

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class _ReaderConnection:
    def __init__(self, connection, entered: Event, release: Event) -> None:
        self._connection = connection
        self._entered = entered
        self._release = release

    def execute(self, sql, *args, **kwargs):
        cursor = self._connection.execute(sql, *args, **kwargs)
        normalized = " ".join(str(sql).lower().split())
        if (
            "from agent_optimizer_observations o" in normalized
            and "max(id) as commit_id" in normalized
            and "limit ?" in normalized
        ):
            return _FetchPauseCursor(cursor, self._entered, self._release)
        return cursor

    def __getattr__(self, name):
        return getattr(self._connection, name)


class _WriterConnection:
    def __init__(self, connection, commit_started: Event) -> None:
        self._connection = connection
        self._commit_started = commit_started
        self._recording = False

    def execute(self, sql, *args, **kwargs):
        normalized = " ".join(str(sql).lower().split())
        if normalized == "begin immediate":
            self._recording = True
        return self._connection.execute(sql, *args, **kwargs)

    def commit(self):
        if self._recording:
            self._commit_started.set()
        return self._connection.commit()

    def __getattr__(self, name):
        return getattr(self._connection, name)


def test_routing_snapshot_keeps_latest_and_history_in_one_paid_evidence_cohort(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_snapshot as runtime_snapshot

    path = tmp_path / "optimizer-observation-snapshot.db"
    stale = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    writer = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(stale)

    monkeypatch.setattr(observation_memory.time, "time", lambda: 1_000.0)
    stale.memory.record_optimizer_observations(
        stale.catalog_key,
        "search",
        _rows(configs, feasible=True),
    )
    monkeypatch.setattr(observation_memory.time, "time", lambda: 2_000.0)

    entered = Event()
    release = Event()
    writer_commit_started = Event()
    stale_connect = stale.memory._connect
    writer_connect = writer.memory._connect

    def reader_connect():
        return _ReaderConnection(stale_connect(), entered, release)

    def concurrent_writer_connect():
        return _WriterConnection(writer_connect(), writer_commit_started)

    monkeypatch.setattr(stale.memory, "_connect", reader_connect)
    monkeypatch.setattr(writer.memory, "_connect", concurrent_writer_connect)

    detected = []
    original_detect = runtime_drift.detect_optimizer_observation_drift

    def capture_detect(*args, **kwargs):
        observations = list(kwargs.get("observations") or [])
        history = list(kwargs.get("observation_history") or [])
        detected.append(
            (
                max((row["updated_at"] for row in observations), default=0.0),
                max((row["observed_at"] for row in history), default=0.0),
                len(observations),
                len(history),
            )
        )
        return original_detect(*args, **kwargs)

    monkeypatch.setattr(runtime_drift, "detect_optimizer_observation_drift", capture_detect)

    snapshot_reads = {"count": 0}
    original_snapshot = runtime_snapshot._read_atomic_snapshot

    def counted_snapshot(*args, **kwargs):
        snapshot_reads["count"] += 1
        return original_snapshot(*args, **kwargs)

    monkeypatch.setattr(runtime_snapshot, "_read_atomic_snapshot", counted_snapshot)

    with ThreadPoolExecutor(max_workers=2) as pool:
        routing_future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        writer_future = pool.submit(
            writer.memory.record_optimizer_observations,
            writer.catalog_key,
            "search",
            _rows(configs, feasible=False, offset=1.0),
        )
        assert writer_commit_started.wait(10.0)
        # The writer has reached COMMIT after updating both tables, but the routing
        # read transaction still owns the old snapshot and therefore keeps commit
        # from becoming visible between the latest/history SELECTs.
        assert not writer_future.done()
        release.set()
        routing_future.result(timeout=15.0)
        writer_future.result(timeout=15.0)

    assert snapshot_reads["count"] == 1
    assert detected[0] == (1_000.0, 1_000.0, len(configs), len(configs))

    manifest = stale.inspect_data()["optimizer_meta_router"]
    state = manifest["optimizer_observation_snapshot_states"]["search"]
    assert state == {
        "status": "coherent_snapshot",
        "latest_rows": len(configs),
        "history_rows": len(configs),
        "history_rows_read": len(configs),
        "history_filtered_rows": 0,
        "latest_newest_at": 1_000.0,
        "history_newest_at": 1_000.0,
        "new_evaluator_calls": 0,
    }
    assert manifest["optimizer_observation_snapshot"] == "single_sqlite_read_transaction"
    assert manifest["optimizer_observation_snapshot_scope"] == "one_routing_decision"
    assert manifest["optimizer_observation_snapshot_history_scope"] == "current_latest_config_set"
    assert manifest["optimizer_observation_snapshot_history_match"] == "exact_durable_config_key"
    assert manifest["optimizer_observation_snapshot_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_snapshot_evaluator_calls"] == 0

    # A new routing decision gets a new snapshot instead of retaining the old one.
    monkeypatch.setattr(stale.memory, "_connect", stale_connect)
    stale._routing_context("search")
    assert snapshot_reads["count"] == 2
    assert detected[-1][0] == 2_000.0
    assert detected[-1][1] == 2_000.0


def test_routing_snapshot_history_is_anchored_to_current_latest_config_set(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_routing_epoch as routing_epoch

    path = tmp_path / "optimizer-observation-current-set-anchor.db"
    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _wide_runtime_configs(registry)
    keyed = sorted(
        (observation_memory._config_key(config), config)
        for config in configs
    )
    read_budget = observation_memory.OPTIMIZER_OBSERVATION_READ_BUDGET
    assert len(keyed) == read_budget + 4
    current = keyed[:read_budget]
    orphaned = keyed[read_budget:]
    current_keys = {key for key, _ in current}
    orphaned_keys = {key for key, _ in orphaned}

    # First give only the four configs that will later fall outside the latest read
    # budget a prior feasibility label. They are legitimate paid history rows.
    monkeypatch.setattr(observation_memory.time, "time", lambda: 1_000.0)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows([config for _, config in orphaned], feasible=False),
    )

    # All 52 configs are then evaluated in one paid cohort. Current-set membership
    # follows retained history commit order, so commit the four configs to exclude
    # first and the 48 current configs after them. History still sees the whole
    # cohort and can manufacture a 100% same-config flip without the anchor.
    monkeypatch.setattr(observation_memory.time, "time", lambda: 2_000.0)
    current_order = [config for _, config in orphaned] + [
        config for _, config in current
    ]
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(current_order, feasible=True, offset=1.0),
    )

    public_latest = registry.memory.optimizer_observations(
        registry.catalog_key,
        "search",
    )
    public_history = registry.memory.optimizer_observation_history(
        registry.catalog_key,
        "search",
    )
    public_latest_keys = {
        observation_memory._config_key(row["config"])
        for row in public_latest
    }
    assert public_latest_keys == current_keys
    assert orphaned_keys.isdisjoint(public_latest_keys)
    assert len(public_history) == len(configs) + len(orphaned)

    dimensions, _ = core._evolution_schema(registry.search.config)
    unanchored = runtime_drift.detect_optimizer_observation_drift(
        dimensions=dimensions,
        observations=public_latest,
        observation_history=public_history,
    )
    assert unanchored["change_detected"] is True
    assert "same_config_feasibility_shift" in unanchored["primary_signals"]
    assert unanchored["same_config_feasibility_pairs"] == 4
    assert unanchored["same_config_feasibility_flip_rate"] == 1.0

    captured = []
    original_detect = runtime_drift.detect_optimizer_observation_drift

    def capture_detect(*args, **kwargs):
        observations = list(kwargs.get("observations") or [])
        history = list(kwargs.get("observation_history") or [])
        captured.append((observations, history))
        return original_detect(*args, **kwargs)

    monkeypatch.setattr(runtime_drift, "detect_optimizer_observation_drift", capture_detect)
    registry._routing_context("search")

    routed_latest, routed_history = captured[-1]
    routed_latest_keys = {
        str(row.get("config_key") or "")
        for row in routed_latest
    }
    routed_history_keys = {
        str(row.get("config_key") or "")
        for row in routed_history
    }
    assert routed_latest_keys == current_keys
    assert routed_history_keys <= routed_latest_keys
    assert orphaned_keys.isdisjoint(routed_history_keys)
    assert len(routed_history) == read_budget

    manifest = registry.inspect_data()["optimizer_meta_router"]
    snapshot_state = manifest["optimizer_observation_snapshot_states"]["search"]
    drift_state = manifest["optimizer_observation_drift_states"]["search"]
    assert snapshot_state["latest_rows"] == read_budget
    assert snapshot_state["history_rows_read"] == len(public_history)
    assert snapshot_state["history_rows"] == read_budget
    assert snapshot_state["history_filtered_rows"] == 2 * len(orphaned)
    assert drift_state["change_detected"] is False
    assert "same_config_feasibility_shift" not in drift_state.get("primary_signals", [])
    assert routing_epoch.routing_epoch_boundary(registry, "search") == 0.0
    assert routing_epoch.pending_routing_epoch_advance(registry, "search") is None
    assert manifest["optimizer_observation_snapshot_history_scope"] == "current_latest_config_set"
    assert manifest["optimizer_observation_snapshot_history_match"] == "exact_durable_config_key"
    assert manifest["optimizer_observation_snapshot_authority"] == "routing_descriptor_only"
    assert manifest["optimizer_observation_snapshot_evaluator_calls"] == 0


def test_public_observation_readers_remain_live_outside_routing_snapshot(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory

    path = tmp_path / "optimizer-observation-public-readers.db"
    registry = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(registry, count=4)

    monkeypatch.setattr(observation_memory.time, "time", lambda: 1_000.0)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(configs, feasible=True),
    )
    first_latest = registry.memory.optimizer_observations(registry.catalog_key, "search")
    first_history = registry.memory.optimizer_observation_history(registry.catalog_key, "search")

    monkeypatch.setattr(observation_memory.time, "time", lambda: 2_000.0)
    registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(configs, feasible=False, offset=1.0),
    )
    second_latest = registry.memory.optimizer_observations(registry.catalog_key, "search")
    second_history = registry.memory.optimizer_observation_history(registry.catalog_key, "search")

    assert max(row["updated_at"] for row in first_latest) == 1_000.0
    assert max(row["observed_at"] for row in first_history) == 1_000.0
    assert max(row["updated_at"] for row in second_latest) == 2_000.0
    assert max(row["observed_at"] for row in second_history) == 2_000.0
    assert len(second_history) == 2 * len(configs)
