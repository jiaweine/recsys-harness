from __future__ import annotations

from lingjing_harness.runtime.memory import AgentMemory
from lingjing_harness.runtime.tools import ToolRegistry
from lingjing_harness.sample_data import build_sample_catalog


def test_inspect_data_reuses_catalog_snapshot_and_returns_safe_copies(monkeypatch):
    catalog = build_sample_catalog()
    calls = 0
    original = catalog.summary

    def counted():
        nonlocal calls
        calls += 1
        return original()

    monkeypatch.setattr(catalog, "summary", counted)
    registry = ToolRegistry(catalog, memory=AgentMemory(":memory:"))
    assert calls == 1

    first = registry.inspect_data()
    second = registry.inspect_data()
    assert calls == 1
    assert first["summary"] == second["summary"]
    assert first["issues"] == second["issues"]

    first["summary"]["items"] = -1
    first["issues"].append("caller mutation")
    third = registry.inspect_data()
    assert third["summary"]["items"] == len(catalog.items)
    assert "caller mutation" not in third["issues"]
    assert calls == 1


def test_task_fork_shares_static_catalog_inspection_snapshot(monkeypatch):
    catalog = build_sample_catalog()
    registry = ToolRegistry(catalog, memory=AgentMemory(":memory:"))

    def fail():
        raise AssertionError("task fork inspection must not rescan Catalog.summary")

    monkeypatch.setattr(catalog, "summary", fail)
    fork = registry.fork()

    assert fork._catalog_inspection is registry._catalog_inspection
    assert fork.inspect_data()["summary"] == registry.inspect_data()["summary"]
