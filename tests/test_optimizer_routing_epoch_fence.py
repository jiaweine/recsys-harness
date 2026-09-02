from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from threading import Event

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.runtime import AgentMemory
from lingjing_harness.runtime.optimizer_meta_memory import OptimizerMetaMemory
from lingjing_harness.runtime.optimizer_routing_checkpoint import (
    OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    OptimizerRoutingCheckpointStore,
)
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


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


def _rows(configs):
    return [
        {
            "config": config,
            "objective": 0.2 + 0.1 * index,
            "feasible": True,
            "source": "epoch_fence_contract_evaluator",
            "generation": index,
            "feasibility_basis": BASIS,
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs)
    ]


def _freeze_runtime_clock(monkeypatch, now):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    monkeypatch.setattr(observation_memory.time, "time", lambda: now)
    monkeypatch.setattr(runtime_drift.time, "time", lambda: now)
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: now)
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: now)


def _block_first_observation_read(monkeypatch, registry):
    entered = Event()
    release = Event()
    original_reader = registry.memory.optimizer_observations
    calls = {"count": 0}

    def blocking_reader(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            entered.set()
            assert release.wait(10.0)
        return original_reader(*args, **kwargs)

    monkeypatch.setattr(registry.memory, "optimizer_observations", blocking_reader)
    return entered, release


def _advance_epoch_from_other_process(registry, *, now, boundary, regime="fallback"):
    store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(registry.memory))
    result = store.record(
        registry.catalog_key,
        "search",
        regime=regime,
        evidence_updated_at=now,
        evidence_seen_count=4,
        evidence_rows=4,
        epoch_started_at=boundary,
        decision_at=now - 500.0,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )
    assert result["evidence_epoch"] == 1
    assert result["epoch_started_at"] == boundary
    return store


def _append_paid_observations(registry, configs):
    result = registry.memory.record_optimizer_observations(
        registry.catalog_key,
        "search",
        _rows(configs),
    )
    assert result["history_rows"] == len(configs)
    assert result["new_evaluator_calls"] == 0


def test_concurrent_epoch_advance_rejects_stale_checkpoint_writer(tmp_path, monkeypatch):
    path = tmp_path / "routing-epoch-cas-writer.db"
    now = 3_000.0
    boundary = 2_000.0
    _freeze_runtime_clock(monkeypatch, now)

    stale = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    winner = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(stale)
    stale.memory.record_optimizer_observations(
        stale.catalog_key,
        "search",
        _rows(configs),
    )

    entered, release = _block_first_observation_read(monkeypatch, stale)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        store = _advance_epoch_from_other_process(
            winner,
            now=now,
            boundary=boundary,
            regime="fallback",
        )
        release.set()
        context = future.result(timeout=15.0)

    checkpoint = store.read(winner.catalog_key, "search", now=now)
    manifest = stale.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]
    fallback = stale._routing_context_without_optimizer_observations("search")

    # Without the expected-epoch CAS, the stale process has the same evidence
    # clock but a later decision_at and could overwrite this fallback with weighted.
    assert checkpoint["regime"] == "fallback"
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["epoch_started_at"] == boundary
    assert checkpoint["decision_at"] == now - 500.0
    assert fence == {
        "status": "epoch_conflict",
        "reason": "concurrent_epoch_advance",
        "action": "pre_observation_fallback",
        "expected_evidence_epoch": 0,
        "expected_epoch_started_at": 0.0,
        "observed_evidence_epoch": 1,
        "observed_epoch_started_at": boundary,
        "expected_observation_revision": len(configs),
        "observed_observation_revision": len(configs),
        "new_evaluator_calls": 0,
    }
    assert context.landscape == fallback.landscape
    assert manifest["optimizer_observation_routing_regimes"]["search"] == "fallback"
    assert manifest["optimizer_observation_routing_epoch_states"]["search"] == {
        "evidence_epoch": 1,
        "epoch_started_at": boundary,
    }
    assert manifest["optimizer_observation_routing_epoch_cas"] == (
        "transactional_expected_epoch_token"
    )
    assert manifest["optimizer_observation_routing_epoch_return_validation"] == (
        "post_decision_checkpoint_revalidation"
    )
    assert manifest["optimizer_observation_routing_epoch_conflict_action"] == (
        "pre_observation_fallback"
    )
    assert manifest["optimizer_observation_routing_revision_fence"] == (
        "history_autoincrement_high_water"
    )
    assert manifest["optimizer_observation_routing_revision_scope"] == (
        "entry_checkpoint_write_and_post_decision_revision_revalidation"
    )
    assert manifest["optimizer_observation_routing_revision_conflict_action"] == (
        "pre_observation_fallback"
    )
    assert manifest["optimizer_observation_routing_epoch_fence_authority"] == (
        "routing_descriptor_only"
    )
    assert manifest["optimizer_observation_routing_epoch_fence_evaluator_calls"] == 0


def test_concurrent_epoch_advance_is_caught_even_without_checkpoint_write(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "routing-epoch-return-revalidation.db"
    now = 4_000.0
    boundary = 3_000.0
    _freeze_runtime_clock(monkeypatch, now)

    stale = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    winner = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(stale, count=3)
    stale.memory.record_optimizer_observations(
        stale.catalog_key,
        "search",
        _rows(configs),
    )

    entered, release = _block_first_observation_read(monkeypatch, stale)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        store = _advance_epoch_from_other_process(
            winner,
            now=now,
            boundary=boundary,
            regime="fallback",
        )
        release.set()
        context = future.result(timeout=15.0)

    checkpoint = store.read(winner.catalog_key, "search", now=now)
    manifest = stale.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]
    fallback = stale._routing_context_without_optimizer_observations("search")

    # Three rows are below the weighted-entry minimum, so the stale call does not
    # need a checkpoint write. Return-time revalidation must still see the advance.
    assert checkpoint["regime"] == "fallback"
    assert checkpoint["evidence_epoch"] == 1
    assert checkpoint["epoch_started_at"] == boundary
    assert fence["status"] == "epoch_conflict"
    assert fence["expected_evidence_epoch"] == 0
    assert fence["observed_evidence_epoch"] == 1
    assert fence["expected_observation_revision"] == len(configs)
    assert fence["observed_observation_revision"] == len(configs)
    assert fence["action"] == "pre_observation_fallback"
    assert context.landscape == fallback.landscape


def test_concurrent_paid_observation_commit_rejects_checkpoint_writer(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "routing-observation-revision-writer.db"
    now = 5_000.0
    _freeze_runtime_clock(monkeypatch, now)

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
    _append_paid_observations(stale, configs)

    entered, release = _block_first_observation_read(monkeypatch, stale)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        # Keep the same frozen timestamp on purpose. The revision fence must rely
        # on the history id high-water rather than wall-clock movement.
        _append_paid_observations(writer, configs)
        release.set()
        context = future.result(timeout=15.0)

    store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(writer.memory))
    checkpoint = store.read(writer.catalog_key, "search", now=now)
    manifest = stale.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]
    fallback = stale._routing_context_without_optimizer_observations("search")

    assert checkpoint is None
    assert fence == {
        "status": "observation_conflict",
        "reason": "concurrent_observation_advance",
        "action": "pre_observation_fallback",
        "expected_evidence_epoch": 0,
        "expected_epoch_started_at": 0.0,
        "observed_evidence_epoch": 0,
        "observed_epoch_started_at": 0.0,
        "expected_observation_revision": len(configs),
        "observed_observation_revision": 2 * len(configs),
        "new_evaluator_calls": 0,
    }
    assert context.landscape == fallback.landscape
    assert manifest["optimizer_observation_routing_regimes"]["search"] == "fallback"
    assert manifest["optimizer_observation_routing_epoch_states"]["search"] == {
        "evidence_epoch": 0,
        "epoch_started_at": 0.0,
    }


def test_concurrent_paid_observation_commit_is_caught_without_checkpoint_write(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "routing-observation-revision-return.db"
    now = 6_000.0
    _freeze_runtime_clock(monkeypatch, now)

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
    configs = _runtime_configs(stale, count=3)
    _append_paid_observations(stale, configs)

    entered, release = _block_first_observation_read(monkeypatch, stale)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        _append_paid_observations(writer, configs)
        release.set()
        context = future.result(timeout=15.0)

    store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(writer.memory))
    checkpoint = store.read(writer.catalog_key, "search", now=now)
    manifest = stale.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]
    fallback = stale._routing_context_without_optimizer_observations("search")

    assert checkpoint is None
    assert fence["status"] == "observation_conflict"
    assert fence["reason"] == "concurrent_observation_advance"
    assert fence["expected_observation_revision"] == len(configs)
    assert fence["observed_observation_revision"] == 2 * len(configs)
    assert fence["action"] == "pre_observation_fallback"
    assert context.landscape == fallback.landscape


def test_concurrent_checkpoint_retirement_fails_closed_for_inflight_rollback_router(
    tmp_path,
    monkeypatch,
):
    import lingjing_harness.runtime.optimizer_observation_drift as runtime_drift
    import lingjing_harness.runtime.optimizer_observation_memory as observation_memory
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as runtime_checkpoint

    path = tmp_path / "routing-checkpoint-retirement-fence.db"
    day = 24.0 * 60.0 * 60.0
    decision_at = 70_000_000.0
    rollback_now = decision_at + 2.0 * day
    expiry_observed_at = decision_at + 4.0 * day
    clock = {"now": decision_at}
    monkeypatch.setattr(observation_memory.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_drift.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_weighting.time, "time", lambda: clock["now"])
    monkeypatch.setattr(runtime_checkpoint.time, "time", lambda: clock["now"])

    stale = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    winner = OptimizerToolRegistry(
        build_sample_catalog(),
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    configs = _runtime_configs(stale)
    _append_paid_observations(stale, configs)
    store = OptimizerRoutingCheckpointStore(OptimizerMetaMemory(winner.memory))
    checkpoint = store.record(
        winner.catalog_key,
        "search",
        regime="weighted",
        evidence_updated_at=decision_at,
        evidence_seen_count=len(configs),
        evidence_rows=len(configs),
        decision_at=decision_at,
        ttl_seconds=OPTIMIZER_OBSERVATION_REGIME_CHECKPOINT_TTL_SECONDS,
    )
    assert checkpoint["recorded"] is True

    clock["now"] = rollback_now
    entered, release = _block_first_observation_read(monkeypatch, stale)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(stale._routing_context, "search")
        assert entered.wait(10.0)
        retired = store.retire_expired(
            winner.catalog_key,
            "search",
            expected_decision_at=checkpoint["decision_at"],
            expected_expires_at=checkpoint["expires_at"],
            observed_at=expiry_observed_at,
        )
        assert retired["retired"] is True
        assert retired["regime"] == "fallback"
        release.set()
        context = future.result(timeout=15.0)

    current = store.read(winner.catalog_key, "search", now=rollback_now)
    manifest = stale.inspect_data()["optimizer_meta_router"]
    fence = manifest["optimizer_observation_routing_epoch_fence_states"]["search"]
    fallback = stale._routing_context_without_optimizer_observations("search")

    assert current["regime"] == "fallback"
    assert current["decision_at"] == expiry_observed_at
    assert current["active_weighted"] is False
    assert context.landscape == fallback.landscape
    assert manifest["optimizer_observation_routing_regimes"]["search"] == "fallback"
    assert fence["action"] == "pre_observation_fallback"
