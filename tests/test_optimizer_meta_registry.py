from contextlib import contextmanager
from dataclasses import asdict

from lingjing_harness.algorithms import evolution_core as core
from lingjing_harness.algorithms.optimizer_meta import build_routing_context
from lingjing_harness.runtime import optimizer_tools
from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


DAY = 24.0 * 60.0 * 60.0


def _availability(**overrides):
    row = {
        "native": True,
        "optuna": False,
        "optuna_motpe": False,
        "qlognehvi": False,
    }
    row.update(overrides)
    return row


def _fake_result(*, replayed: bool = False):
    return {
        "objective_delta": 0.05,
        "candidate_count": 10,
        "candidate_config": {"candidate": "stable"},
        "evaluation_ready": True,
        "replayed": replayed,
        "evolution": {
            "method": "mixed_genome_response_surface",
            "response_surface": [{}, {}],
        },
    }


def _durable_search_observations(registry, count=4):
    dimensions, group_totals = core._evolution_schema(registry.search.config)
    base = asdict(registry.search.config)
    configs = []
    seen = set()
    for dimension in dimensions:
        for _, _, config in core._neighbors(base, dimension, dimensions, group_totals):
            key = repr(sorted(config.items()))
            if key in seen:
                continue
            seen.add(key)
            configs.append(config)
            if len(configs) >= count:
                break
        if len(configs) >= count:
            break
    assert len(configs) >= count
    return [
        {
            "config": config,
            "objective": 0.2 + 0.05 * index,
            "feasible": index >= count // 2,
            "source": "paid_test",
            "generation": index,
            "feasibility_basis": "search_discovery_robustness_guardrails",
            "constraints": {"worse_share": 0.1, "worst_delta": -0.1},
        }
        for index, config in enumerate(configs[:count])
    ]


def test_auto_registry_is_opt_in_and_native_remains_default(monkeypatch):
    catalog = build_sample_catalog()
    default = OptimizerToolRegistry(catalog)
    auto = OptimizerToolRegistry(catalog, optimizer_backend="auto")

    assert default.optimizer_backend == "native"
    assert auto.optimizer_backend == "auto"
    assert default.inspect_data()["optimizer_backends"] == [
        "native",
        "optuna",
        "optuna_motpe",
        "qlognehvi",
    ]
    assert auto.inspect_data()["optimizer_meta_router"]["enabled"] is True


def test_auto_registry_falls_back_to_native_without_optional_dependencies(monkeypatch):
    monkeypatch.setattr(
        optimizer_tools,
        "optimizer_dependency_availability",
        lambda: _availability(),
    )
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")

    result = registry._run_auto("search", lambda **kwargs: _fake_result())
    meta = result["evolution"]["optimizer_meta_router"]

    assert meta["selected_backend"] == "native"
    assert meta["authority"] == "optimizer_selection_only"
    assert meta["promotion_authority"] == "downstream_holdout_and_trust"
    assert result["optimizer_meta_credit"]["recorded"] is True


def test_auto_dependency_preflight_falls_through_before_runner(monkeypatch):
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    context = build_routing_context(
        surface="search",
        evidence_route="production",
        evaluation_budget=24,
        dimensions=[
            type("D", (), {"kind": "continuous", "choices": ()})(),
            type("D", (), {"kind": "continuous", "choices": ()})(),
            type("D", (), {"kind": "capability", "choices": ("a", "b", "c")})(),
        ],
        cache={index: None for index in range(8)},
        objective_count=2,
        constraint_count=2,
    )
    monkeypatch.setattr(registry, "_routing_context", lambda surface: context)
    monkeypatch.setattr(
        optimizer_tools,
        "optimizer_dependency_availability",
        lambda: _availability(optuna=True, optuna_motpe=True, qlognehvi=True),
    )

    order = []

    @contextmanager
    def preflight(backend):
        order.append(f"preflight:{backend}")
        if backend == "qlognehvi":
            raise RuntimeError("broken optional qlog stack")
        yield backend

    monkeypatch.setattr(optimizer_tools, "select_optimizer_backend", preflight)

    def runner(**kwargs):
        order.append("runner")
        return _fake_result()

    result = registry._run_auto("search", runner)
    meta = result["evolution"]["optimizer_meta_router"]

    assert order[0] == "preflight:qlognehvi"
    assert order.index("runner") > 0
    assert meta["selected_backend"] != "qlognehvi"
    assert meta["preflight_failures"] == {"qlognehvi": "RuntimeError"}


def test_replay_does_not_double_count_optimizer_credit(monkeypatch):
    monkeypatch.setattr(
        optimizer_tools,
        "optimizer_dependency_availability",
        lambda: _availability(),
    )
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")

    first = registry._run_auto(
        "search",
        lambda **kwargs: _fake_result(replayed=False),
        _invocation_id="optimizer-run-1",
    )
    second = registry._run_auto(
        "search",
        lambda **kwargs: _fake_result(replayed=True),
        _invocation_id="optimizer-run-1",
    )
    rows = registry.optimizer_meta_memory.read(registry.catalog_key, "search")

    assert first["optimizer_meta_credit"]["recorded"] is True
    assert second["optimizer_meta_credit"]["recorded"] is False
    assert len(rows) == 1
    assert rows[0]["trials"] == 1


def test_auto_policy_survives_registry_fork():
    registry = OptimizerToolRegistry(build_sample_catalog(), optimizer_backend="auto")
    child = registry.fork()

    assert child.optimizer_backend == "auto"
    assert child.optimizer_meta_memory is registry.optimizer_meta_memory


def test_durable_routing_checkpoint_preserves_restart_hysteresis_but_not_confidence(
    tmp_path,
):
    import lingjing_harness.runtime.optimizer_observation_weighting as runtime_weighting
    import lingjing_harness.runtime.optimizer_routing_checkpoint as routing_checkpoint

    catalog = build_sample_catalog()
    path = tmp_path / "memory.db"
    first = OptimizerToolRegistry(
        catalog,
        memory=AgentMemory(path),
        optimizer_backend="auto",
    )
    first.memory.record_optimizer_observations(
        first.catalog_key,
        "search",
        _durable_search_observations(first, 4),
    )

    base_time = 8_000_000_000.0
    with first.memory._lock:
        connection = first.memory._connect()
        try:
            connection.execute(
                "update agent_optimizer_observations set updated_at=? where catalog_key=? and domain='search'",
                (base_time, first.catalog_key),
            )
            connection.commit()
        finally:
            first.memory._close(connection)

    original_time = routing_checkpoint.time.time
    routing_checkpoint.time.time = lambda: base_time
    try:
        fresh = first._routing_context("search")
        fresh_manifest = first.inspect_data()["optimizer_meta_router"]
        checkpoint = first._optimizer_routing_checkpoint_store.read(
            first.catalog_key,
            "search",
            now=base_time,
        )
        assert fresh.landscape.informative is True
        assert checkpoint is not None
        assert checkpoint["regime"] == "weighted"
        assert checkpoint["active_weighted"] is True
        assert fresh_manifest["optimizer_observation_regime_checkpoint_authority"] == "routing_hysteresis_only"
        assert fresh_manifest["optimizer_observation_regime_checkpoint_evaluator_calls"] == 0

        routing_checkpoint.time.time = lambda: base_time + 2 * DAY
        second = OptimizerToolRegistry(
            catalog,
            memory=AgentMemory(path),
            optimizer_backend="auto",
        )
        mid_rows = second.memory.optimizer_observations(second.catalog_key, "search")
        mid_diagnostics = runtime_weighting.optimizer_observation_weight_diagnostics(
            runtime_weighting.weight_optimizer_observations(
                mid_rows,
                reference_time=base_time + 2 * DAY,
            )
        )
        mid = second._routing_context("search")
        mid_manifest = second.inspect_data()["optimizer_meta_router"]

        assert mid_diagnostics["enter_confident"] is False
        assert mid_diagnostics["stay_confident"] is True
        assert mid.landscape.informative is True
        assert mid_manifest["optimizer_observation_routing_regimes"]["search"] == "weighted"

        routing_checkpoint.time.time = lambda: base_time + 4 * DAY
        third = OptimizerToolRegistry(
            catalog,
            memory=AgentMemory(path),
            optimizer_backend="auto",
        )
        exit_rows = third.memory.optimizer_observations(third.catalog_key, "search")
        exit_diagnostics = runtime_weighting.optimizer_observation_weight_diagnostics(
            runtime_weighting.weight_optimizer_observations(
                exit_rows,
                reference_time=base_time + 4 * DAY,
            )
        )
        after = third._routing_context("search")
        fallback = third._routing_context_without_optimizer_observations("search")
        after_manifest = third.inspect_data()["optimizer_meta_router"]
    finally:
        routing_checkpoint.time.time = original_time

    assert exit_diagnostics["stay_confident"] is False
    assert after.context_key == fallback.context_key
    assert after.landscape.to_dict() == fallback.landscape.to_dict()
    assert after_manifest["optimizer_observation_routing_regimes"]["search"] == "fallback"
