from __future__ import annotations

import sqlite3

from lingjing_harness.runtime.memory import AgentMemory


def _record_episode(memory: AgentMemory, catalog_key: str, goal: str = "goal") -> None:
    memory.record_episode(
        catalog_key,
        goal,
        "audit",
        0.5,
        findings=[],
        action_keys=[],
        learned=[],
    )


def test_file_backed_stats_reuse_exact_snapshot_until_data_changes(tmp_path, monkeypatch):
    memory = AgentMemory(tmp_path / "agent-memory.db")
    calls = 0
    original = memory._stats_uncached

    def counted(catalog_key=None):
        nonlocal calls
        calls += 1
        return original(catalog_key)

    monkeypatch.setattr(memory, "_stats_uncached", counted)

    first = memory.stats("catalog-a")
    second = memory.stats("catalog-a")

    assert first == second
    assert calls == 1

    first["episodes"] = 999
    assert memory.stats("catalog-a")["episodes"] == 0
    assert calls == 1


def test_stats_cache_invalidates_after_same_process_durable_write(tmp_path):
    memory = AgentMemory(tmp_path / "agent-memory.db")

    assert memory.stats("catalog-a")["episodes"] == 0
    _record_episode(memory, "catalog-a")

    assert memory.stats("catalog-a")["episodes"] == 1


def test_stats_cache_invalidates_after_other_memory_instance_write(tmp_path):
    path = tmp_path / "agent-memory.db"
    reader = AgentMemory(path)
    writer = AgentMemory(path)

    assert reader.stats("catalog-a")["episodes"] == 0
    _record_episode(writer, "catalog-a", goal="other worker")

    assert reader.stats("catalog-a")["episodes"] == 1


def test_stats_cache_invalidates_after_external_sqlite_commit(tmp_path):
    path = tmp_path / "agent-memory.db"
    memory = AgentMemory(path)

    assert memory.stats("catalog-a")["episodes"] == 0

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            insert into agent_episodes(
              catalog_key,goal,mode,reward,payload,created_at
            ) values(?,?,?,?,?,?)
            """,
            ("catalog-a", "external", "audit", 0.5, "{}", 1.0),
        )
        connection.commit()

    assert memory.stats("catalog-a")["episodes"] == 1


def test_in_memory_stats_deliberately_bypass_data_version_cache(monkeypatch):
    memory = AgentMemory(":memory:")
    calls = 0
    original = memory._stats_uncached

    def counted(catalog_key=None):
        nonlocal calls
        calls += 1
        return original(catalog_key)

    monkeypatch.setattr(memory, "_stats_uncached", counted)

    memory.stats("catalog-a")
    memory.stats("catalog-a")

    assert calls == 2
