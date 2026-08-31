from contextlib import contextmanager

from lingjing_harness.algorithms.optimizer_meta import build_routing_context
from lingjing_harness.runtime import optimizer_tools
from lingjing_harness.runtime.optimizer_tools import OptimizerToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


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
